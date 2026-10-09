(function () {
  var C = window.RINGO || {};

  function toast(msg) {
    var t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove("show"); }, 1800);
  }

  function copy(text) {
    var done = function () { toast("Copied " + text); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else { fallback(); }
    function fallback() {
      var a = document.createElement("textarea"); a.value = text; document.body.appendChild(a);
      a.select(); try { document.execCommand("copy"); done(); } catch (e) {} a.remove();
    }
  }

  document.querySelectorAll("[data-copy]").forEach(function (el) {
    el.addEventListener("click", function () { copy(el.getAttribute("data-copy") || C.REF_CODE); });
  });

  // contact fill-ins
  document.querySelectorAll("[data-tg]").forEach(function (el) {
    el.href = "https://t.me/" + C.TELEGRAM; el.textContent = el.textContent || "@" + C.TELEGRAM;
    el.target = "_blank"; el.rel = "noopener";
  });
  document.querySelectorAll("[data-phone]").forEach(function (el) {
    el.href = "tel:+1" + C.PHONE.replace(/\D/g, ""); if (!el.textContent) el.textContent = C.PHONE;
  });
  document.querySelectorAll("[data-ref]").forEach(function (el) {
    el.href = C.REF_LINK; el.target = "_blank"; el.rel = "noopener";
  });

  // google form embeds
  document.querySelectorAll("[data-form]").forEach(function (box) {
    var key = box.getAttribute("data-form");
    var url = C["FORM_" + key + "_URL"];
    var h = C["FORM_" + key + "_HEIGHT"] || 1200;
    if (url) {
      box.className = "form-frame";
      box.innerHTML = '<iframe src="' + url + '" height="' + h + '" loading="lazy" title="RinGoMining form">Loading…</iframe>';
    }
  });

  // sensitive image reveal
  document.querySelectorAll(".sensitive button").forEach(function (b) {
    b.addEventListener("click", function () { b.parentElement.classList.add("open"); });
  });
})();
