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
    name: "1234972623", phone: "981600907", email: "74004195", telegram: "166926812",
    amount: "668052535", coin: "362201095", contact: "677394826", questions: "447858836"
  },
  FORM_STEP3_ID: "1FAIpQLScyyyqncXMLBvEnuPbySYCatVUxgiNN41iwND8ilgJKbC_hAQ",
  ENTRIES_STEP3: {
    email: "96833558", gmid: "654544234", coin: "916926981",
    chain: "1918993367", wallet: "505577898", notes: "1775599723"
  },

  // Admin lead tracker backend (Apps Script web app URL ending in /exec).
  // Protected by your username/password, which are never stored in this repo.
  ADMIN_API: "https://script.google.com/macros/s/AKfycbw1EZtvAbIjBbC9wiene_4K7Gus9erTAjSMC-BS0nOpGv9dnfGeTwAvOt6fqMBlvMJL/exec",

  FORM_STEP3_URL: "https://docs.google.com/forms/d/e/1FAIpQLScyyyqncXMLBvEnuPbySYCatVUxgiNN41iwND8ilgJKbC_hAQ/viewform?embedded=true",
  FORM_STEP3_HEIGHT: 1100
};
