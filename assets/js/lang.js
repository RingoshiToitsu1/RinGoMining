(function () {
  // Language helper: remembers a chosen language and, on English pages, offers
  // the visitor's own language (French, Spanish, German) when a version exists.
  var MSG = {
    fr: ["Ce site est disponible en français.", "Voir en français"],
    es: ["Este sitio está disponible en español.", "Ver en español"],
    de: ["Diese Seite gibt es auch auf Deutsch.", "Auf Deutsch ansehen"]
  };
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  var here = (document.documentElement.lang || "en").slice(0, 2);
  var alts = {};
  document.querySelectorAll('link[rel="alternate"][hreflang]').forEach(function (l) {
    var h = l.getAttribute("hreflang");
    if (h !== "x-default") alts[h.slice(0, 2)] = l.getAttribute("href");
  });

  // remember explicit choices from the language menu
  document.querySelectorAll("[data-setlang]").forEach(function (a) {
    a.addEventListener("click", function () { set("ringo_lang", a.getAttribute("data-setlang")); });
  });

  if (here !== "en" || get("ringo_lang_dismiss")) return;
  var pref = get("ringo_lang");
  if (pref === "en") return;
  var want = null;
  if (pref && MSG[pref] && alts[pref]) want = pref;
  if (!want) {
    var langs = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ""];
    for (var i = 0; i < langs.length; i++) {
      var c = String(langs[i]).slice(0, 2).toLowerCase();
      if (c === "en") return;              // they read English first: leave them alone
      if (MSG[c] && alts[c]) { want = c; break; }
    }
  }
  if (!want) return;

  var bar = document.createElement("div");
  bar.className = "lang-suggest";
  bar.setAttribute("lang", want);
  bar.innerHTML = '<span></span><a class="go"></a><button type="button" aria-label="Close">×</button>';
  bar.querySelector("span").textContent = MSG[want][0];
  var go = bar.querySelector(".go");
  go.textContent = MSG[want][1] + " →";
  go.href = alts[want];
  go.addEventListener("click", function () { set("ringo_lang", want); });
  bar.querySelector("button").addEventListener("click", function () {
    set("ringo_lang_dismiss", "1"); set("ringo_lang", "en"); bar.remove();
  });
  document.body.insertBefore(bar, document.body.firstChild);
})();
