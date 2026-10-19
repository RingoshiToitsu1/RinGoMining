/**
 * RinGoMining: instant emails when someone claims their bonus.
 *
 * Every time the claim form (ringomining.com/apply) is submitted:
 *  1. The person gets a confirmation email from you right away, in their
 *     language (English, French, Spanish or German).
 *  2. You get an alert email with their GoMining ID and wallet details,
 *     so you can check your referral list and pay fast.
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
  var claim = { email: me, gmid: '3566334', coin: 'USDC', chain: 'BNB Chain',
                wallet: '0xTEST', notes: '@yourname\nLanguage: EN' };
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
    subject: 'Got your RINGO5 claim',
    body: 'Hey,\n\nRingo here. I got your claim for the RINGO5 bonus.\n\nGoMining ID: {gmid}\nPaid in: {coin}\n\n' +
      "Here's what happens next: I check your ID against my referral list and confirm your purchase. " +
      'Then I send your bonus within 24 hours of confirming.\n\n' +
      "If you haven't bought your terahash yet, go ahead whenever you're ready. Your 10% back is based on what you buy.\n\n" +
      'Questions? Just reply to this email, or message me on Telegram: {tg}\n\nRingo\nringomining.com'
  },
  fr: {
    subject: 'Demande RINGO5 bien reçue',
    body: 'Bonjour,\n\nIci Ringo. J\'ai bien reçu votre demande de bonus RINGO5.\n\nID GoMining : {gmid}\nPaiement en : {coin}\n\n' +
      'La suite : je vérifie votre ID dans ma liste de filleuls et je confirme votre achat. ' +
      'Ensuite, j\'envoie votre bonus sous 24 heures après vérification.\n\n' +
      'Si vous n\'avez pas encore acheté votre térahash, faites-le quand vous êtes prêt. Vos 10 % remboursés sont calculés sur ce que vous achetez.\n\n' +
      'Des questions ? Répondez simplement à cet e-mail, ou écrivez-moi sur Telegram : {tg}\n\nRingo\nringomining.com'
  },
  es: {
    subject: 'Recibí tu solicitud RINGO5',
    body: 'Hola,\n\nSoy Ringo. Recibí tu solicitud del bono RINGO5.\n\nID de GoMining: {gmid}\nPago en: {coin}\n\n' +
      'Lo que sigue: reviso tu ID en mi lista de referidos y confirmo tu compra. ' +
      'Después te envío el bono dentro de las 24 horas tras confirmarlo.\n\n' +
      'Si todavía no compraste tu terahash, hazlo cuando estés listo. Tu 10 % de vuelta se calcula sobre lo que compres.\n\n' +
      '¿Preguntas? Responde a este correo o escríbeme por Telegram: {tg}\n\nRingo\nringomining.com'
  },
  de: {
    subject: 'Deine RINGO5-Anfrage ist da',
    body: 'Hey,\n\nhier ist Ringo. Ich habe deine Anfrage für den RINGO5-Bonus bekommen.\n\nGoMining-ID: {gmid}\nAuszahlung in: {coin}\n\n' +
      'So geht es weiter: Ich prüfe deine ID in meiner Empfehlungsliste und bestätige deinen Kauf. ' +
      'Dann schicke ich deinen Bonus innerhalb von 24 Stunden nach der Bestätigung.\n\n' +
      'Falls du deinen Terahash noch nicht gekauft hast, mach das, wenn du bereit bist. Deine 10 % zurück richten sich nach deinem Kauf.\n\n' +
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
    'Email: ' + claim.email + '\nGoMining ID: ' + claim.gmid + '\nPaid in: ' + claim.coin +
    '\nChain: ' + claim.chain + '\nWallet: ' + claim.wallet +
    (claim.notes ? '\nNotes: ' + claim.notes : '') +
    '\n\nCheck the ID against your GoMining referral list, then update it in the tracker: ' + CLAIM_ADMIN;
  MailApp.sendEmail({ to: me, subject: 'New RINGO5 claim: ID ' + (claim.gmid || '?') + ' (' + claim.email + ')',
                      body: body, replyTo: claim.email || me, name: 'RinGoMining claims' });
}
