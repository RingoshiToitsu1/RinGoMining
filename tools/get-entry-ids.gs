/**
 * RinGoMining: prints the hidden field IDs of both Google Forms so the
 * site can use its own themed form and still send answers to your Sheet.
 *
 * HOW TO RUN: paste into the same Apps Script project (or a new one),
 * Save, choose "getEntryIds" in the dropdown, click Run, then copy the whole
 * Execution log and send it to Claude.
 */
function getEntryIds() {
  var forms = {
    STEP1: '1taH2h0UIghKfvPvvMlG_2NjDaGiq3vDPHXzHHMCUGLA',
    STEP3: '1qVEYmz46RDppP6Cpwely2XpGuw15nOjLz4MVr8mCkGE'
  };
  Object.keys(forms).forEach(function (key) {
    var form = FormApp.openById(forms[key]);
    Logger.log('=== ' + key + ' ===');
    form.getItems().forEach(function (item) {
      var title = item.getTitle();
      var resp;
      switch (item.getType()) {
        case FormApp.ItemType.TEXT:
          var sample = /mail/i.test(title) ? 'a@b.co' : (/GoMining ID/i.test(title) ? '1' : 'x');
          resp = item.asTextItem().createResponse(sample); break;
        case FormApp.ItemType.PARAGRAPH_TEXT:
          resp = item.asParagraphTextItem().createResponse('x'); break;
        case FormApp.ItemType.MULTIPLE_CHOICE:
          var mc = item.asMultipleChoiceItem();
          resp = mc.createResponse(mc.getChoices()[0].getValue()); break;
        default:
          return;
      }
      var url = form.createResponse().withItemResponse(resp).toPrefilledUrl();
      var m = url.match(/entry\.(\d+)=/);
      Logger.log(title + ' => entry.' + (m ? m[1] : '???'));
    });
  });
}
