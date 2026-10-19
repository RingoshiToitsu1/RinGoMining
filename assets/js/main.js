(function () {
  var C = window.RINGO || {};
  var T = {
    en: { copied: "Copied ", fill: "Please fill in: ", sending: "Sending…", fail: "Couldn't send. Check your connection and try again, or message me on Telegram." },
    fr: { copied: "Copié : ", fill: "Merci de remplir : ", sending: "Envoi…", fail: "Envoi impossible. Vérifiez votre connexion et réessayez, ou écrivez-moi sur Telegram." },
    es: { copied: "Copiado: ", fill: "Completa: ", sending: "Enviando…", fail: "No se pudo enviar. Revisa tu conexión e inténtalo de nuevo, o escríbeme por Telegram." },
    de: { copied: "Kopiert: ", fill: "Bitte ausfüllen: ", sending: "Wird gesendet…", fail: "Senden fehlgeschlagen. Prüfe deine Verbindung und versuch es nochmal, oder schreib mir auf Telegram." }
  };
  var LANG = (document.documentElement.lang || "en").slice(0, 2);
  var L = T[LANG] || T.en;

  // old funnel steps (/signup, /verify) forward to the one-page claim flow
  var go = document.querySelector("[data-redirect]");
  if (go) { location.replace(go.href); return; }

  function toast(msg) {
    var t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove("show"); }, 1800);
  }

  function copy(text) {
    var done = function () { toast(L.copied + text); };
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

  // native themed forms -> post into Google Forms
  var nativeReady = {};
  document.querySelectorAll("form[data-native]").forEach(function (form) {
    var key = form.getAttribute("data-native");
    var map = C["ENTRIES_" + key] || {};
    var id = C["FORM_" + key + "_ID"];
    var ok = id && Object.keys(map).length && Object.keys(map).every(function (k) { return map[k]; });
    if (!ok) return;
    nativeReady[key] = true;
    form.hidden = false;
    document.querySelectorAll("[data-embed-only]").forEach(function (el) { el.hidden = true; });

    var err = form.querySelector(".form-error");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      err.hidden = true;
      if (!form.checkValidity()) {
        var bad = form.querySelector(":invalid");
        var f = bad && bad.closest(".field");
        var name = f ? (f.querySelector("label, .lbl").firstChild.textContent || "").trim() : "a field";
        err.textContent = L.fill + name;
        err.hidden = false;
        if (bad) bad.focus();
        return;
      }
      var vals = {};
      new FormData(form).forEach(function (v, k) { vals[k] = String(v).trim(); });
      // tag non-English claims so the reply goes out in their language
      if (map.notes && LANG !== "en") vals.notes = (vals.notes ? vals.notes + "\n" : "") + "Language: " + LANG.toUpperCase();
      var data = new URLSearchParams();
      Object.keys(vals).forEach(function (k) {
        if (map[k] && vals[k]) data.append("entry." + map[k], vals[k]);
      });
      var btn = form.querySelector("button[type=submit]");
      var label = btn.textContent; btn.disabled = true; btn.textContent = L.sending;
      fetch("https://docs.google.com/forms/d/e/" + id + "/formResponse", {
        method: "POST", mode: "no-cors", body: data
      }).then(function () {
        form.hidden = true;
        var s = document.querySelector('[data-success="' + key + '"]');
        if (s) { s.hidden = false; s.scrollIntoView({ behavior: "smooth", block: "center" }); }
        try { localStorage.setItem("ringo_" + key, "1"); } catch (x) {}
      }).catch(function () {
        btn.disabled = false; btn.textContent = label;
        err.textContent = L.fail;
        err.hidden = false;
      });
    });
  });

  // google form embeds (fallback when native form isn't configured)
  document.querySelectorAll("[data-form]").forEach(function (box) {
    var key = box.getAttribute("data-form");
    if (nativeReady[key]) { box.remove(); return; }
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
