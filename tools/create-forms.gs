/**
 * RinGoMining: creates both funnel Google Forms + one response Sheet.
 *
 * HOW TO RUN
 * 1. Go to https://script.google.com and click "New project".
 * 2. Delete everything in the editor and paste this whole file in.
 * 3. Click Save (disk icon), then pick "createRinGoForms" in the function
 *    dropdown at the top and click Run.
 * 4. Approve the permissions prompt (Advanced → Go to project → Allow).
 *    It only asks to create Forms and Sheets in your own Google Drive.
 * 5. When it finishes, open "Execution log" at the bottom. Copy the two
 *    EMBED URLs and send them to Claude (or paste them into
 *    assets/js/config.js yourself).
 */
function createRinGoForms() {
  var sheet = SpreadsheetApp.create('RinGoMining Leads');

  // ---------- FORM 1: Step 1, Your info ----------
  var f1 = FormApp.create('RinGoMining · Step 1: Your Info');
  f1.setDescription(
    "Tell me who you are so I can confirm you're coming in on code RINGO5 " +
    "and reach you to pay your bonus. Takes about a minute."
  );
  f1.setConfirmationMessage(
    "Got it! Now go back to the RinGoMining page and tap \"I submitted it. Step 2 →\" " +
    "to create your GoMining account with code RINGO5."
  );
  f1.setShowLinkToRespondAgain(false);
  f1.setProgressBar(false);

  f1.addTextItem().setTitle('Full name').setRequired(true);

  f1.addTextItem().setTitle('Phone number')
    .setHelpText('Include your country code if you are outside the US.');

  f1.addTextItem().setTitle('Email').setRequired(true)
    .setHelpText('Use the same email you will sign up to GoMining with.')
    .setValidation(FormApp.createTextValidation()
      .requireTextIsEmail()
      .setHelpText('Please enter a valid email address.')
      .build());

  f1.addTextItem().setTitle('Telegram handle')
    .setHelpText('Example: @yourname');

  f1.addMultipleChoiceItem().setTitle('How much are you planning to start with?')
    .setChoiceValues([
      'Just the first terahash',
      '$100 – $499',
      '$500 – $999',
      '$1,000 – $4,999',
      '$5,000 – $10,000',
      '$10,000+'
    ])
    .setRequired(true);

  f1.addMultipleChoiceItem().setTitle('How do you want your bonus paid?')
    .setChoiceValues(['USDC', 'Bitcoin', 'GoMining token (GMT)'])
    .setRequired(true);

  f1.addMultipleChoiceItem().setTitle('Best way to reach you?')
    .setChoiceValues(['Telegram', 'Text', 'Call', 'Email'])
    .setRequired(true);

  f1.addParagraphTextItem().setTitle('Questions or concerns?')
    .setHelpText("Anything holding you back? Ask. I'll answer straight.");

  f1.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());

  // ---------- FORM 2: Step 3, Confirm & get paid ----------
  var f2 = FormApp.create('RinGoMining · Step 3: Confirm & Get Paid');
  f2.setDescription(
    'Last step. I use your GoMining ID to confirm you signed up under RINGO5, ' +
    'then I send your bonus within 24 hours of confirmation.'
  );
  f2.setConfirmationMessage(
    "You're in. I'll confirm your referral and reach out on the contact method you gave me. " +
    'Bonus paid within 24 hours of confirmation. Welcome to the team.'
  );
  f2.setShowLinkToRespondAgain(false);

  f2.addTextItem().setTitle('Email you used in Step 1').setRequired(true)
    .setValidation(FormApp.createTextValidation()
      .requireTextIsEmail()
      .setHelpText('Please enter a valid email address.')
      .build());

  f2.addTextItem().setTitle('GoMining ID').setRequired(true)
    .setHelpText('In GoMining, open your Profile. Your ID is the number under your name.')
    .setValidation(FormApp.createTextValidation()
      .requireNumber()
      .setHelpText('Numbers only, e.g. 3566334')
      .build());

  f2.addMultipleChoiceItem().setTitle('Payout coin')
    .setChoiceValues(['USDC', 'Bitcoin', 'GoMining token (GMT)'])
    .setRequired(true);

  f2.addTextItem().setTitle('Chain / network').setRequired(true)
    .setHelpText('Example: Ethereum, BNB Chain, Polygon, Solana, Bitcoin. Must match your wallet address.');

  f2.addTextItem().setTitle('Wallet address').setRequired(true)
    .setHelpText('Double-check it. Crypto sent to a wrong address cannot be recovered.');

  f2.addParagraphTextItem().setTitle('Anything else I should know?');

  f2.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());

  // ---------- Output ----------
  var embed1 = f1.getPublishedUrl() + '?embedded=true';
  var embed2 = f2.getPublishedUrl() + '?embedded=true';

  Logger.log('=== COPY THESE ===');
  Logger.log('FORM_STEP1_URL (Your Info):   ' + embed1);
  Logger.log('FORM_STEP3_URL (Get Paid):    ' + embed2);
  Logger.log('');
  Logger.log('Edit Form 1:     ' + f1.getEditUrl());
  Logger.log('Edit Form 2:     ' + f2.getEditUrl());
  Logger.log('Responses Sheet: ' + sheet.getUrl());
}
