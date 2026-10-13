/**
 * RinGoMining: renames the "Just the first terahash" answer to "Under $100"
 * on the Step 1 Google Form ("How much are you planning to start with?").
 *
 * The question keeps its ID, so the website, the Leads sheet column and the
 * admin tracker keep working. Answers already submitted keep the old text.
 *
 * HOW TO RUN
 * 1. Go to https://script.google.com and click "New project", signed in to
 *    the Google account that owns the RinGoMining forms.
 * 2. Delete everything in the editor and paste this whole file in.
 * 3. Click Save, pick "renameAmountChoice" in the function dropdown, click Run.
 * 4. Approve the permissions prompt (Advanced → Go to project → Allow).
 * 5. Open "Execution log" at the bottom. It should say "Renamed".
 *
 * Run it at the same time the website update goes live, so the site and the
 * form send and expect the same answer.
 */
var STEP1_PUBLISHED_ID = '1FAIpQLScOK7XSQGdnT_YSp_F5lWhD6iAQhEGl_h1CiCEVTOmNLYeGCQ';
var QUESTION = 'How much are you planning to start with?';
var OLD_CHOICE = 'Just the first terahash';
var NEW_CHOICE = 'Under $100';

function renameAmountChoice() {
  var form = findStep1Form_();
  var item = form.getItems(FormApp.ItemType.MULTIPLE_CHOICE).filter(function (it) {
    return it.getTitle() === QUESTION;
  })[0];
  if (!item) throw new Error('Could not find the question "' + QUESTION + '" on ' + form.getTitle());

  var mc = item.asMultipleChoiceItem();
  var values = mc.getChoices().map(function (c) { return c.getValue(); });
  if (values.indexOf(NEW_CHOICE) !== -1 && values.indexOf(OLD_CHOICE) === -1) {
    Logger.log('Already done. Choices: ' + values.join(' | '));
    return;
  }
  if (values.indexOf(OLD_CHOICE) === -1) {
    throw new Error('"' + OLD_CHOICE + '" is not one of the choices: ' + values.join(' | '));
  }

  var renamed = values.map(function (v) { return v === OLD_CHOICE ? NEW_CHOICE : v; });
  mc.setChoices(renamed.map(function (v) { return mc.createChoice(v); }));
  Logger.log('Renamed "' + OLD_CHOICE + '" to "' + NEW_CHOICE + '" on ' + form.getTitle());
  Logger.log('Choices now: ' + renamed.join(' | '));
}

// Finds the Step 1 form in your Drive by matching its published link.
function findStep1Form_() {
  var files = DriveApp.getFilesByType(MimeType.GOOGLE_FORMS);
  while (files.hasNext()) {
    var form = FormApp.openById(files.next().getId());
    if (form.getPublishedUrl().indexOf(STEP1_PUBLISHED_ID) !== -1) return form;
  }
  throw new Error('Step 1 form not found. Run this from the Google account that owns the RinGoMining forms.');
}
