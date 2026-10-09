/**
 * RinGoMining Admin backend (Google Apps Script web app).
 *
 * It reads your RinGoMining Leads sheet, merges Step 1 + Step 3 answers by
 * email, and saves your tracking info (status, notes, payouts) to a
 * "Tracker" tab in the same sheet. The admin page on your site talks to it,
 * and every request must carry your username and password.
 *
 * SETUP (one time)
 * 1. In the same Apps Script project as your forms: Files ＋ → Script,
 *    name it "admin", paste this whole file.
 * 2. Type your password between the quotes on the ADMIN_PASS line below.
 *    (Do this only in Apps Script. Never commit your password to GitHub.)
 * 3. Save, pick "setupAdmin" in the dropdown → Run. The log should say
 *    "Login saved". Then delete the password from ADMIN_PASS and Save again.
 *    Only a scrambled (hashed) copy is kept, in Script Properties.
 * 4. Deploy → New deployment → gear icon → "Web app".
 *      Description: RinGoMining Admin
 *      Execute as:  Me
 *      Who has access: Anyone   (the login is what keeps it private)
 *    Deploy → Authorize → copy the Web app URL (ends in /exec).
 * 5. Send the Web app URL to Claude.
 *
 * If you ever edit this file: Deploy → Manage deployments → pencil →
 * Version: "New version" → Deploy (the URL stays the same).
 * To change your login: put the new values below, run setupAdmin again,
 * then clear ADMIN_PASS.
 */

var ADMIN_USER = 'ringoshi';
var ADMIN_PASS = '';   // type it here only to run setupAdmin, then clear it

var SHEET_ID = '1crOVTfGgO32181PsKMcKg4vy59HfblryuzYsCVq-6pQ';
var STEP1_TAB = 'Form Responses 1';
var STEP3_TAB = 'Form Responses 2';
var TRACK_TAB = 'Tracker';
var TRACK_COLS = ['email', 'status', 'purchase', 'bonus_paid', 'paid_coin', 'tx', 'notes',
                  'reached_out_at', 'confirmed_at', 'paid_at', 'updated_at'];

function setupAdmin() {
  if (!ADMIN_PASS) throw new Error('Type your password into ADMIN_PASS first.');
  var salt = Utilities.getUuid();
  var props = PropertiesService.getScriptProperties();
  props.setProperties({
    ADMIN_USER: ADMIN_USER.trim().toLowerCase(),
    ADMIN_SALT: salt,
    ADMIN_HASH: hash_(salt + ADMIN_PASS)
  });
  props.deleteProperty('ADMIN_KEY');
  trackerSheet_();
  Logger.log('Login saved for user "' + ADMIN_USER + '". Now clear ADMIN_PASS and Save.');
}

function hash_(s) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s, Utilities.Charset.UTF_8);
  return bytes.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
}

// true if user/pass match; blocks for 15 min after 10 bad tries
function checkLogin_(user, pass) {
  var cache = CacheService.getScriptCache();
  var fails = Number(cache.get('fails') || 0);
  if (fails >= 10) return 'locked';
  var p = PropertiesService.getScriptProperties();
  var ok = p.getProperty('ADMIN_HASH') &&
    String(user || '').trim().toLowerCase() === p.getProperty('ADMIN_USER') &&
    hash_(p.getProperty('ADMIN_SALT') + String(pass || '')) === p.getProperty('ADMIN_HASH');
  if (!ok) { cache.put('fails', String(fails + 1), 900); Utilities.sleep(800); }
  return ok ? 'ok' : 'bad';
}

function doGet(e) {
  return handle_(e && e.parameter ? e.parameter : {});
}

function doPost(e) {
  var body = {};
  try { body = JSON.parse(e.postData.contents || '{}'); } catch (err) {}
  return handle_(body);
}

function handle_(req) {
  try {
    var auth = checkLogin_(req.user, req.pass);
    if (auth === 'locked') return json_({ ok: false, error: 'locked' });
    if (auth !== 'ok') return json_({ ok: false, error: 'unauthorized' });
    if (req.action === 'login') return json_({ ok: true });
    if (req.action === 'list') return json_({ ok: true, leads: list_() });
    if (req.action === 'update') return json_({ ok: true, lead: update_(req.email, req.fields || {}) });
    return json_({ ok: false, error: 'unknown action' });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function ss_() { return SpreadsheetApp.openById(SHEET_ID); }

function trackerSheet_() {
  var ss = ss_();
  var sh = ss.getSheetByName(TRACK_TAB);
  if (!sh) {
    sh = ss.insertSheet(TRACK_TAB);
    sh.getRange(1, 1, 1, TRACK_COLS.length).setValues([TRACK_COLS]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  return sh;
}

function rows_(name) {
  var sh = ss_().getSheetByName(name);
  if (!sh || sh.getLastRow() < 2) return [];
  var vals = sh.getDataRange().getValues();
  var head = vals.shift();
  return vals.map(function (r) {
    var o = {};
    head.forEach(function (h, i) {
      var v = r[i];
      o[h] = v instanceof Date ? v.toISOString() : v;
    });
    return o;
  });
}

function norm_(email) { return String(email || '').trim().toLowerCase(); }

function list_() {
  var leads = {};
  function get(email) {
    var k = norm_(email);
    if (!k) return null;
    if (!leads[k]) leads[k] = { email: k, step1: null, step3: null, track: {} };
    return leads[k];
  }
  rows_(STEP1_TAB).forEach(function (r) {
    var l = get(r['Email']); if (!l) return;
    l.step1 = {
      at: r['Timestamp'], name: r['Full name'], phone: r['Phone number'],
      telegram: r['Telegram handle'], amount: r['How much are you planning to start with?'],
      coin: r['How do you want your bonus paid?'], contact: r['Best way to reach you?'],
      questions: r['Questions or concerns?']
    };
  });
  rows_(STEP3_TAB).forEach(function (r) {
    var l = get(r['Email you used in Step 1']); if (!l) return;
    l.step3 = {
      at: r['Timestamp'], gmid: r['GoMining ID'], coin: r['Payout coin'],
      chain: r['Chain / network'], wallet: r['Wallet address'], notes: r['Anything else I should know?']
    };
  });
  rows_(TRACK_TAB).forEach(function (r) {
    var l = get(r.email); if (!l) return;
    var t = {};
    TRACK_COLS.forEach(function (c) { t[c] = r[c] === undefined ? '' : r[c]; });
    l.track = t;
  });
  return Object.keys(leads).map(function (k) { return leads[k]; });
}

function update_(email, fields) {
  var k = norm_(email);
  if (!k) throw new Error('missing email');
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var sh = trackerSheet_();
    var vals = sh.getDataRange().getValues();
    var rowIdx = -1;
    for (var i = 1; i < vals.length; i++) {
      if (norm_(vals[i][0]) === k) { rowIdx = i; break; }
    }
    var row = rowIdx > 0 ? vals[rowIdx] : TRACK_COLS.map(function () { return ''; });
    var cur = {};
    TRACK_COLS.forEach(function (c, i) { cur[c] = row[i] instanceof Date ? row[i].toISOString() : row[i]; });

    var now = new Date().toISOString();
    var editable = ['status', 'purchase', 'bonus_paid', 'paid_coin', 'tx', 'notes'];
    editable.forEach(function (c) {
      if (fields[c] !== undefined) cur[c] = String(fields[c]).slice(0, 2000);
    });
    // timestamps the first time a status is reached
    if (cur.status === 'Reached out' && !cur.reached_out_at) cur.reached_out_at = now;
    if (cur.status === 'Confirmed' && !cur.confirmed_at) cur.confirmed_at = now;
    if (cur.status === 'Paid' && !cur.paid_at) cur.paid_at = now;
    cur.email = k;
    cur.updated_at = now;

    var out = TRACK_COLS.map(function (c) { return cur[c]; });
    if (rowIdx > 0) sh.getRange(rowIdx + 1, 1, 1, out.length).setValues([out]);
    else sh.appendRow(out);
    return cur;
  } finally {
    lock.releaseLock();
  }
}
