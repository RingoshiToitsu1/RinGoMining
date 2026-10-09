/*
  RinGoMining funnel settings. Edit these values, commit, and the site updates.

  FORM_STEP1_URL  -> Google Form "Step 1: Your info" embed URL
  FORM_STEP3_URL  -> Google Form "Step 3: Confirm & get paid" embed URL

  To get an embed URL: open the form in Google Forms > Send > the "< >" tab >
  copy the src="..." link from the code (it ends in ?embedded=true).
  Leave a value as "" and the page shows a Telegram/phone fallback instead.
*/
window.RINGO = {
  REF_CODE: "RINGO5",
  REF_LINK: "https://gomining.com/?ref=RINGO5",
  TELEGRAM: "RingoShitoitsu",
  PHONE: "906-235-5711",

  FORM_STEP1_URL: "https://docs.google.com/forms/d/e/1FAIpQLScOK7XSQGdnT_YSp_F5lWhD6iAQhEGl_h1CiCEVTOmNLYeGCQ/viewform?embedded=true",
  FORM_STEP1_HEIGHT: 1500,

  // Native (themed) forms post straight into the Google Forms above.
  // Fill in the entry IDs from tools/get-entry-ids.gs. While any are blank,
  // the page falls back to the embedded Google Form.
  FORM_STEP1_ID: "1FAIpQLScOK7XSQGdnT_YSp_F5lWhD6iAQhEGl_h1CiCEVTOmNLYeGCQ",
  ENTRIES_STEP1: {
    name: "", phone: "", email: "", telegram: "",
    amount: "", coin: "", contact: "", questions: ""
  },
  FORM_STEP3_ID: "1FAIpQLScyyyqncXMLBvEnuPbySYCatVUxgiNN41iwND8ilgJKbC_hAQ",
  ENTRIES_STEP3: {
    email: "", gmid: "", coin: "", chain: "", wallet: "", notes: ""
  },

  FORM_STEP3_URL: "https://docs.google.com/forms/d/e/1FAIpQLScyyyqncXMLBvEnuPbySYCatVUxgiNN41iwND8ilgJKbC_hAQ/viewform?embedded=true",
  FORM_STEP3_HEIGHT: 1100
};
