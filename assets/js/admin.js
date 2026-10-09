(function () {
  var API = (window.RINGO || {}).ADMIN_API;
  var STATUSES = ["New", "Reached out", "Waiting on purchase", "Confirmed", "Paid", "Not a fit"];
  var creds = null, leads = [], filter = "All", query = "", openSet = {};

  var $ = function (s) { return document.querySelector(s); };
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function toast(m) { var t = $(".toast"); t.textContent = m; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove("show"); }, 1800); }
  function money(n) { n = Number(n) || 0; return "$" + n.toLocaleString(undefined, { maximumFractionDigits: 2 }); }
  function ago(iso) {
    if (!iso) return "";
    var s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (isNaN(s)) return "";
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function fmt(iso) { if (!iso) return ""; var d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString(); }
  function store(get, val) {
    try {
      if (get) return JSON.parse(localStorage.getItem("ringo_admin") || sessionStorage.getItem("ringo_admin") || "null");
      localStorage.removeItem("ringo_admin"); sessionStorage.removeItem("ringo_admin");
      if (val) (val.remember ? localStorage : sessionStorage).setItem("ringo_admin", JSON.stringify(val));
    } catch (e) { return null; }
  }

  function call(body) {
    if (!API) return Promise.reject(new Error("Admin backend URL isn't set in assets/js/config.js (ADMIN_API)."));
    body.user = creds.user; body.pass = creds.pass;
    return fetch(API, { method: "POST", headers: { "Content-Type": "text/plain;charset=utf-8" }, body: JSON.stringify(body) })
      .catch(function () {
        throw new Error("Can't reach the backend. In Apps Script, check the deployment's access is set to \"Anyone\".");
      })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) {
          var e = new Error(j.error === "unauthorized" ? "Wrong username or password." :
                            j.error === "locked" ? "Too many tries. Wait 15 minutes." : (j.error || "Something went wrong."));
          e.code = j.error; throw e;
        }
        return j;
      });
  }

  // ---------- login ----------
  function showApp(on) {
    $("#loginView").hidden = on; $("#appView").hidden = !on; $("#barActions").hidden = !on;
  }
  $("#loginForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var err = $("#loginErr"), btn = $("#loginBtn");
    err.hidden = true;
    creds = { user: $("#u").value.trim(), pass: $("#p").value, remember: $("#remember").checked };
    if (!creds.user || !creds.pass) { err.textContent = "Enter your username and password."; err.hidden = false; return; }
    btn.disabled = true; btn.textContent = "Checking…";
    call({ action: "list" }).then(function (j) {
      store(false, creds); leads = j.leads; showApp(true); render();
    }).catch(function (x) {
      err.textContent = x.message; err.hidden = false; creds = null;
    }).then(function () { btn.disabled = false; btn.textContent = "Log in"; });
  });
  $("#logoutBtn").addEventListener("click", function () { store(false, null); creds = null; leads = []; $("#p").value = ""; showApp(false); });
  $("#refreshBtn").addEventListener("click", function () { load(true); });

  function load(announce) {
    return call({ action: "list" }).then(function (j) { leads = j.leads; render(); if (announce) toast("Updated"); })
      .catch(function (x) {
        if (x.code === "unauthorized") { store(false, null); creds = null; showApp(false); }
        toast(x.message);
      });
  }

  // ---------- derived ----------
  function statusOf(l) { return (l.track && l.track.status) || "New"; }
  function nameOf(l) { return (l.step1 && l.step1.name) || l.email; }
  function lastAt(l) {
    return [l.step1 && l.step1.at, l.step3 && l.step3.at, l.track && l.track.updated_at].filter(Boolean).sort().pop() || "";
  }
  function isDue(l) {
    // confirmed but not paid for 18h+ (you promise 24h)
    return statusOf(l) === "Confirmed" && l.track.confirmed_at && (Date.now() - new Date(l.track.confirmed_at)) > 18 * 3600e3;
  }
  function suggested(purchase) {
    var p = Number(purchase) || 0;
    if (!p) return 0;
    return Math.round((Math.min(p, 10000) * 0.10 + 18.99) * 100) / 100;
  }

  // ---------- render ----------
  function render() {
    var counts = { All: leads.length };
    STATUSES.forEach(function (s) { counts[s] = 0; });
    var paid = 0, purchases = 0;
    leads.forEach(function (l) {
      counts[statusOf(l)] = (counts[statusOf(l)] || 0) + 1;
      paid += Number(l.track.bonus_paid) || 0;
      purchases += Number(l.track.purchase) || 0;
    });
    var dueN = leads.filter(isDue).length;

    $("#stats").innerHTML =
      stat("Leads", counts.All) +
      stat("Need reach-out", counts["New"], counts["New"] ? "hot" : "") +
      stat("ID sent, not confirmed", leads.filter(function (l) { return l.step3 && ["Confirmed", "Paid", "Not a fit"].indexOf(statusOf(l)) < 0; }).length) +
      stat("Awaiting payout", counts["Confirmed"] + (dueN ? " ⚠" : ""), counts["Confirmed"] ? "gold" : "") +
      stat("Bonuses paid", money(paid), "gold") +
      stat("Referred purchases", money(purchases));

    $("#tabs").innerHTML = ["All"].concat(STATUSES).map(function (s) {
      return '<button type="button" role="tab" aria-selected="' + (s === filter) + '" data-tab="' + esc(s) + '">' + esc(s) + "<span>" + (counts[s] || 0) + "</span></button>";
    }).join("");

    var q = query.toLowerCase();
    var rows = leads.filter(function (l) {
      if (filter !== "All" && statusOf(l) !== filter) return false;
      if (!q) return true;
      return JSON.stringify(l).toLowerCase().indexOf(q) >= 0;
    }).sort(function (a, b) {
      var ra = isDue(a) ? 0 : statusOf(a) === "New" ? 1 : 2, rb = isDue(b) ? 0 : statusOf(b) === "New" ? 1 : 2;
      return ra - rb || (lastAt(b) > lastAt(a) ? 1 : -1);
    });

    $("#list").innerHTML = rows.map(card).join("");
    $("#empty").hidden = rows.length > 0;
  }
  function stat(label, val, cls) { return '<div class="stat ' + (cls || "") + '"><small>' + esc(label) + "</small><b>" + esc(val) + "</b></div>"; }

  function card(l) {
    var s1 = l.step1 || {}, s3 = l.step3 || {}, t = l.track || {}, st = statusOf(l), key = esc(l.email);
    var badges = [];
    badges.push(l.step1 ? '<span class="badge ok">Step 1</span>' : '<span class="badge bad">No Step 1</span>');
    badges.push(l.step3 ? '<span class="badge ok">ID sent</span>' : '<span class="badge warn">No ID yet</span>');
    if (s1.amount) badges.push('<span class="badge">' + esc(s1.amount) + "</span>");
    if (isDue(l)) badges.push('<span class="badge bad">Pay now · confirmed ' + esc(ago(t.confirmed_at)) + "</span>");

    var tg = String(s1.telegram || "").replace(/^@/, "").trim();
    var phone = String(s1.phone || "").replace(/[^\d+]/g, "");
    var contacts = [];
    if (tg) contacts.push('<a href="https://t.me/' + encodeURIComponent(tg) + '" target="_blank" rel="noopener">Telegram @' + esc(tg) + "</a>");
    if (phone) contacts.push('<a href="sms:' + esc(phone) + '">Text</a><a href="tel:' + esc(phone) + '">Call</a>');
    contacts.push('<a href="mailto:' + esc(l.email) + '?subject=' + encodeURIComponent("Your RINGO5 bonus") + '">Email</a>');

    var coin = t.paid_coin || s3.coin || s1.coin || "";
    var open = !!openSet[l.email];

    return '<article class="lead' + (isDue(l) ? " due" : "") + '" data-email="' + key + '">' +
      '<div class="lead-head" data-toggle>' +
        '<div class="who"><strong>' + esc(nameOf(l)) + "</strong>" +
          '<div class="sub">' + esc(l.email) + (s3.gmid ? " · GoMining ID " + esc(s3.gmid) : "") + " · " + esc(ago(lastAt(l))) + "</div>" +
          '<div class="badges">' + badges.join("") + "</div></div>" +
        '<span class="pill-status st-' + esc(st.replace(/ /g, "-")) + '">' + esc(st) + "</span>" +
      "</div>" +
      '<div class="lead-body"' + (open ? "" : " hidden") + ">" +
        "<div>" +
          "<h4>Contact" + (s1.contact ? " · prefers " + esc(s1.contact) : "") + "</h4>" +
          '<div class="contact-btns">' + contacts.join("") + "</div>" +
          "<h4>Step 1</h4>" +
          (l.step1 ? '<dl class="kv">' +
            kv("Name", s1.name) + kv("Phone", s1.phone) + kv("Telegram", s1.telegram) +
            kv("Plans to start", s1.amount) + kv("Bonus coin", s1.coin) + kv("Submitted", fmt(s1.at)) + "</dl>" +
            (s1.questions ? '<p class="quote">' + esc(s1.questions) + "</p>" : "")
            : '<p class="muted">Didn\'t fill out Step 1.</p>') +
          "<h4>Step 3 · payout details</h4>" +
          (l.step3 ? '<dl class="kv">' +
            kv("GoMining ID", s3.gmid, true) + kv("Coin", s3.coin) + kv("Chain", s3.chain) +
            kv("Wallet", s3.wallet, true) + kv("Submitted", fmt(s3.at)) + "</dl>" +
            (s3.notes ? '<p class="quote">' + esc(s3.notes) + "</p>" : "")
            : '<p class="muted">Hasn\'t sent their GoMining ID yet.</p>') +
        "</div>" +
        '<form class="track" data-form>' +
          "<h4>Tracking</h4>" +
          '<div class="status-row">' + STATUSES.map(function (s) {
            return '<button type="button" data-status="' + esc(s) + '" class="' + (s === st ? "on" : "") + '">' + esc(s) + "</button>";
          }).join("") + "</div>" +
          '<input type="hidden" name="status" value="' + esc(st) + '">' +
          '<div class="two">' +
            '<div class="field"><label>Their purchase ($)</label><input name="purchase" inputmode="decimal" value="' + esc(t.purchase) + '" placeholder="0"></div>' +
            '<div class="field"><label>Bonus paid ($)</label><input name="bonus_paid" inputmode="decimal" value="' + esc(t.bonus_paid) + '" placeholder="0"></div>' +
          "</div>" +
          '<p class="hint" data-suggest>' + suggestText(t.purchase) + "</p>" +
          '<div class="two" style="margin-top:12px">' +
            '<div class="field"><label>Paid in</label><select name="paid_coin">' +
              ["", "USDC", "Bitcoin", "GoMining token (GMT)"].map(function (c) {
                return '<option value="' + esc(c) + '"' + (c === coin ? " selected" : "") + ">" + (c ? esc(c) : "Choose…") + "</option>";
              }).join("") + "</select></div>" +
            '<div class="field"><label>Tx hash / link</label><input name="tx" value="' + esc(t.tx) + '" placeholder="0x… or explorer link" spellcheck="false"></div>' +
          "</div>" +
          '<div class="field"><label>Notes</label><textarea name="notes" rows="3" placeholder="Private notes">' + esc(t.notes) + "</textarea></div>" +
          '<div class="save-row"><button class="btn btn-fire" type="submit">Save</button><span class="saved" hidden>Saved ✓</span></div>' +
          '<div class="times">' +
            (t.reached_out_at ? "Reached out: " + esc(fmt(t.reached_out_at)) + "<br>" : "") +
            (t.confirmed_at ? "Confirmed: " + esc(fmt(t.confirmed_at)) + "<br>" : "") +
            (t.paid_at ? "Paid: " + esc(fmt(t.paid_at)) + "<br>" : "") +
            (t.updated_at ? "Last saved: " + esc(fmt(t.updated_at)) : "") +
          "</div>" +
        "</form>" +
      "</div>" +
    "</article>";
  }
  function kv(k, v, copy) {
    if (v === "" || v == null) return "";
    return "<dt>" + esc(k) + "</dt><dd>" + (copy ? "<code>" + esc(v) + '</code><button type="button" class="copy" data-copyval="' + esc(v) + '">Copy</button>' : esc(v)) + "</dd>";
  }
  function suggestText(p) {
    var s = suggested(p);
    return s ? "Suggested bonus: " + money(s) + " (10% of up to $10,000 + $18.99 first terahash)" : "Enter their purchase to see the suggested bonus.";
  }

  // ---------- events ----------
  $("#tabs").addEventListener("click", function (e) {
    var b = e.target.closest("[data-tab]"); if (!b) return; filter = b.getAttribute("data-tab"); render();
  });
  $("#search").addEventListener("input", function (e) { query = e.target.value; render(); });

  $("#list").addEventListener("click", function (e) {
    var art = e.target.closest(".lead"); if (!art) return;
    var email = art.getAttribute("data-email");
    if (e.target.closest("[data-toggle]")) {
      openSet[email] = !openSet[email];
      art.querySelector(".lead-body").hidden = !openSet[email];
      return;
    }
    var c = e.target.closest("[data-copyval]");
    if (c) {
      var v = c.getAttribute("data-copyval");
      (navigator.clipboard ? navigator.clipboard.writeText(v) : Promise.reject()).then(function () { toast("Copied"); }, function () { toast(v); });
      return;
    }
    var sb = e.target.closest("[data-status]");
    if (sb) {
      var form = sb.closest("form");
      form.querySelectorAll("[data-status]").forEach(function (x) { x.classList.toggle("on", x === sb); });
      form.status.value = sb.getAttribute("data-status");
    }
  });
  $("#list").addEventListener("input", function (e) {
    if (e.target.name === "purchase") {
      e.target.closest("form").querySelector("[data-suggest]").textContent = suggestText(e.target.value);
    }
  });
  $("#list").addEventListener("submit", function (e) {
    e.preventDefault();
    var form = e.target, art = form.closest(".lead"), email = art.getAttribute("data-email");
    var fields = {};
    ["status", "purchase", "bonus_paid", "paid_coin", "tx", "notes"].forEach(function (n) { fields[n] = form[n].value.trim(); });
    var btn = form.querySelector("button[type=submit]"); btn.disabled = true; btn.textContent = "Saving…";
    call({ action: "update", email: email, fields: fields }).then(function (j) {
      leads.forEach(function (l) { if (l.email === email) l.track = j.lead; });
      openSet[email] = true;
      render();
      toast("Saved");
    }).catch(function (x) { toast(x.message); btn.disabled = false; btn.textContent = "Save"; });
  });

  // ---------- boot ----------
  var saved = store(true);
  if (saved && saved.user) {
    creds = saved; $("#u").value = saved.user;
    showApp(true);
    $("#list").innerHTML = '<p class="empty">Loading…</p>';
    load(false);
  } else if (!API) {
    var err = $("#loginErr"); err.textContent = "Not connected yet: the admin backend URL still needs to be added."; err.hidden = false;
  }
})();
