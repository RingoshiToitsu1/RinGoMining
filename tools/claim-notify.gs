/**
 * RinGoMining: instant emails when someone sends their GoMining ID.
 *
 * Every time the claim form (ringomining.com/apply) is submitted:
 *  1. The person gets a confirmation email from you right away, in their
 *     language (English, French, Spanish or German).
 *  2. You get an alert email with their GoMining ID,
 *     so you can check your referral list and reach out fast.
 *
 * HOW TO SET UP (one time)
 * 1. Open your existing RinGoMining Apps Script project (the one with
 *    createRinGoForms and the admin backend).
 * 2. Files ＋ → Script, name it "claim-notify", paste this whole file.
 * 3. Save. Pick "testClaimEmails" in the dropdown → Run → approve the
 *    permissions prompt. Two sample emails land in YOUR inbox only.
 * 4. Pick "installClaimEmails" → Run. The log should say "Installed".
 *    From then on it runs by itself. Running it again is safe.
 *
 * Emails go out from your Gmail, so replies come straight to you.
 */
var CLAIM_SHEET_ID = '1crOVTfGgO32181PsKMcKg4vy59HfblryuzYsCVq-6pQ';
var CLAIM_TAB = 'Form Responses 2';
var CLAIM_TELEGRAM = 'https://t.me/RinGoMining';
var CLAIM_ADMIN = 'https://ringomining.com/admin';

function installClaimEmails() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'onClaimSubmit') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('onClaimSubmit')
    .forSpreadsheet(CLAIM_SHEET_ID)
    .onFormSubmit()
    .create();
  Logger.log('Installed. Every new claim now sends a confirmation email and alerts you.');
}

function testClaimEmails() {
  var me = Session.getEffectiveUser().getEmail();
  var claim = { email: me, gmid: '3566334', coin: 'GoMining token (GMT)', chain: 'BNB Smart Chain (BEP-20)',
                wallet: '0x0000000000000000000000000000000000000000', notes: '@yourname\nLanguage: EN' };
  sendCustomerEmail_(claim);
  sendOwnerAlert_(claim);
  Logger.log('Sent two test emails to ' + me);
}

function onClaimSubmit(e) {
  if (!e || !e.range || e.range.getSheet().getName() !== CLAIM_TAB) return;  // ignore the old Step 1 form
  var v = e.namedValues || {};
  var one = function (k) { return String((v[k] || [''])[0] || '').trim(); };
  var claim = {
    email: one('Email you used in Step 1'),
    gmid: one('GoMining ID'),
    coin: one('Payout coin'),
    chain: one('Chain / network'),
    wallet: one('Wallet address'),
    notes: one('Anything else I should know?')
  };
  try { if (claim.email) sendCustomerEmail_(claim); } catch (err) { Logger.log('Customer email failed: ' + err); }
  sendOwnerAlert_(claim);
}

function langOf_(claim) {
  var m = /Language:\s*(FR|ES|DE)/i.exec(claim.notes || '');
  return m ? m[1].toLowerCase() : 'en';
}

var CLAIM_MAIL = {
  en: {
    subject: 'Got your GoMining ID',
    body: 'Hey,\n\nRingo here. Got your GoMining ID: {gmid}\n\n' +
      "I'll check it against my referral list to confirm you're under my code RINGO5.\n\n" +
      "If you haven't bought your terahash yet, go ahead whenever you're ready. GoMining adds 5% bonus terahash to your first miner.\n\n" +
      'Questions? Just reply to this email, or message me on Telegram: {tg}\n\nRingo\nringomining.com'
  },
  fr: {
    subject: 'ID GoMining bien reçu',
    body: 'Bonjour,\n\nIci Ringo. J\'ai bien reçu votre ID GoMining : {gmid}\n\n' +
      'Je le vérifie dans ma liste de filleuls pour confirmer que vous êtes inscrit avec mon code RINGO5.\n\n' +
      'Si vous n\'avez pas encore acheté votre térahash, faites-le quand vous êtes prêt. GoMining ajoute 5 % de térahash bonus à votre premier mineur.\n\n' +
      'Des questions ? Répondez simplement à cet e-mail, ou écrivez-moi sur Telegram : {tg}\n\nRingo\nringomining.com'
  },
  es: {
    subject: 'Recibí tu ID de GoMining',
    body: 'Hola,\n\nSoy Ringo. Recibí tu ID de GoMining: {gmid}\n\n' +
      'Lo reviso en mi lista de referidos para confirmar que estás con mi código RINGO5.\n\n' +
      'Si todavía no compraste tu terahash, hazlo cuando estés listo. GoMining añade un 5 % de terahash de bono a tu primer minero.\n\n' +
      '¿Preguntas? Responde a este correo o escríbeme por Telegram: {tg}\n\nRingo\nringomining.com'
  },
  de: {
    subject: 'Deine GoMining-ID ist da',
    body: 'Hey,\n\nhier ist Ringo. Ich habe deine GoMining-ID bekommen: {gmid}\n\n' +
      'Ich prüfe sie in meiner Empfehlungsliste, um zu bestätigen, dass du über meinen Code RINGO5 dabei bist.\n\n' +
      'Falls du deinen Terahash noch nicht gekauft hast, mach das, wenn du bereit bist. GoMining legt 5 % Bonus-Terahash auf deinen ersten Miner drauf.\n\n' +
      'Fragen? Antworte einfach auf diese E-Mail oder schreib mir auf Telegram: {tg}\n\nRingo\nringomining.com'
  }
};

function fill_(tpl, claim) {
  return tpl.replace('{gmid}', claim.gmid || '-').replace('{coin}', claim.coin || '-').replace('{tg}', CLAIM_TELEGRAM);
}

function sendCustomerEmail_(claim) {
  var m = CLAIM_MAIL[langOf_(claim)];
  MailApp.sendEmail({ to: claim.email, subject: m.subject, body: fill_(m.body, claim), name: 'Ringo · RinGoMining' });
}

function sendOwnerAlert_(claim) {
  var me = Session.getEffectiveUser().getEmail();
  var body = 'New claim on ringomining.com/apply\n\n' +
    'Email: ' + claim.email + '\nGoMining ID: ' + claim.gmid +
    (claim.notes ? '\nNotes: ' + claim.notes : '') +
    '\n\nCheck the ID against your GoMining referral list, then update it in the tracker: ' + CLAIM_ADMIN;
  MailApp.sendEmail({ to: me, subject: 'New RINGO5 claim: ID ' + (claim.gmid || '?') + ' (' + claim.email + ')',
                      body: body, replyTo: claim.email || me, name: 'RinGoMining claims' });
}
