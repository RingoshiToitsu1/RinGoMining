/*
  RinGoMining co-pilot: a scripted guide in Ringo's voice. No AI, no API, no backend.
  It walks people through sign up -> buy -> send GoMining ID, answers the common questions,
  sends their ID (posts to the same Google Form as /apply), and hands anything
  off-script to Ringo on Telegram or text.

  Edit the wording in NODES below. Each node has:
    say:   lines Ringo says (trusted HTML, written here only)
    links: buttons that open something (ref link, Telegram, text, a page)
    img:   optional screenshot
    opts:  reply buttons -> { t: label, go: next node }
    ask:   collect a value -> { key, label, type, check, bad, next }
*/
(function () {
  if ((document.documentElement.lang || "en").slice(0, 2) !== "en") return; // English pages only for now
  var C = window.RINGO || {};
  var REF = C.REF_LINK || "https://gomining.com/?ref=RINGO5";
  var CODE = C.REF_CODE || "RINGO5";
  var TG = "https://t.me/" + (C.TELEGRAM || "RinGoMining");
  var PHONE = C.PHONE || "906-235-5711";
  var SMS = "sms:+1" + PHONE.replace(/\D/g, "");
  var STORE = "ringo_copilot";

  var NODES = {
    start: {
      say: ["What's up, Ringo Mining here.", "I'll walk you through getting set up with my code <b>" + CODE + "</b>. About five minutes. Where are you at?"],
      opts: [
        { t: "Brand new, let's go", go: "signup" },
        { t: "I already have a GoMining account", go: "existing" },
        { t: "I already bought", go: "claim" },
        { t: "I've got questions first", go: "faq" }
      ]
    },
    signup: {
      say: ["<b>Step 1 of 3.</b> Tap the button. It opens GoMining in a new tab with my code already on, so this chat stays right here.", "Create your account. If it asks for a referral code, type <b>" + CODE + "</b>."],
      links: [{ t: "Open GoMining ↗", kind: "ref" }, { t: "Copy " + CODE, kind: "copy" }],
      opts: [
        { t: "Done, account made ✓", go: "buy" },
        { t: "It never asked for a code", go: "nocode" },
        { t: "I'm stuck", go: "stuck" }
      ]
    },
    nocode: {
      say: ["That's normal. The link puts the code on for you.", "If you want to be sure, check your profile in GoMining. Accounts less than 30 days old can still add <b>" + CODE + "</b> there. Do it before you buy anything."],
      opts: [{ t: "Got it, next step", go: "buy" }, { t: "I'm stuck", go: "stuck" }]
    },
    existing: {
      say: ["Real talk: GoMining's new-user bonus only works on accounts created with <b>" + CODE + "</b>.", "If your account is less than 30 days old, add " + CODE + " in your profile before you buy. Older than that? Message me before you buy and we'll figure it out."],
      opts: [
        { t: "Added it ✓", go: "buy" },
        { t: "My account is older", go: "human" },
        { t: "Back", go: "start" }
      ]
    },
    buy: {
      say: ["<b>Step 2 of 3.</b> Buy your terahash, any amount.", "With my code, GoMining adds <b>5% bonus terahash</b> to your first miner."],
      opts: [
        { t: "Bought it ✓", go: "claim" },
        { t: "How much should I start with?", go: "howmuch" },
        { t: "Questions first", go: "faq" }
      ]
    },
    howmuch: {
      say: ["That's your call, not mine. Only put in money you can afford to lose.", "Run your numbers in the calculator first. It shows what I'd actually expect at different amounts."],
      links: [{ t: "Open the calculator", href: "/#calculator" }],
      opts: [{ t: "OK, I bought ✓", go: "claim" }, { t: "Back to step 2", go: "buy" }]
    },
    claim: {
      say: ["<b>Step 3 of 3.</b> Send me your GoMining ID so I can confirm you're under my code.", "I need two things: your email and your GoMining ID. I'll fill it in for you as we go."],
      opts: [{ t: "Let's do it", go: "q_email" }, { t: "Wait, I haven't bought yet", go: "buy" }]
    },
    q_email: {
      say: ["What's your email? That's where I'll get back to you."],
      ask: { key: "email", label: "Email", type: "email", check: /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/, bad: "That email doesn't look right. Try again?", next: "q_id" }
    },
    q_id: {
      say: ["Now your GoMining ID. In GoMining, tap your profile picture (top right). It's the number right under your name."],
      img: { src: "/assets/img/find-your-id.png", alt: "GoMining profile card with the ID number under the name highlighted" },
      ask: { key: "gmid", label: "GoMining ID", type: "text", mode: "numeric", check: /^\d{3,}$/, bad: "Just the numbers, like 3566334.", next: "q_tg" }
    },
    q_tg: {
      say: ["Want to drop your Telegram handle so I can reach you faster? Totally optional."],
      ask: { key: "tg", label: "Telegram handle", type: "text", optional: true, next: "review" }
    },
    review: { say: [], review: true },
    sent: {
      say: ["Got it. 🔥", "I check your GoMining ID against my referral list to confirm you're under my code. Watch your email.", "Welcome to the team."],
      links: [{ t: "Message me on Telegram", kind: "tg" }],
      opts: [{ t: "I've got a question", go: "faq" }]
    },
    sendfail: {
      say: ["Hmm, that didn't send. Check your connection and try again, or send me your ID on Telegram and I'll set you up by hand."],
      links: [{ t: "Message me on Telegram", kind: "tg" }],
      opts: [{ t: "Try again", go: "review" }]
    },
    faq: {
      say: ["Ask away. What's on your mind?"],
      opts: [
        { t: "Is GoMining legit?", go: "f_legit" },
        { t: "How do I get money to my bank?", go: "f_bank" },
        { t: "What's in it for you?", go: "f_you" },
        { t: "I don't really get crypto", go: "f_newbie" },
        { t: "Talk to Ringo", go: "human" }
      ]
    },
    f_legit: {
      say: ["Fair question. It's the same risk as any investment. If you own a company's stock and it falls apart, your shares fall with it.", "GoMining has been around since 2017 and has offered this product since 2021. I trust them with my own money. But no investment is risk-free, and this one isn't either."],
      links: [{ t: "Read my full review", href: "/gomining-review/" }],
      opts: [{ t: "Another question", go: "faq" }, { t: "Let's get started", go: "signup" }]
    },
    f_bank: {
      say: ["Here's how I do it. I withdraw on BNB Smart Chain, swap to USDC, send it to Coinbase, then to my bank.", "Or skip all that and spend it with the GoMining Visa card through Apple Pay."],
      links: [{ t: "Open Coinbase ↗", href: "https://coinbase.com/join/MC6T569" }],
      opts: [{ t: "Another question", go: "faq" }, { t: "Let's get started", go: "signup" }]
    },
    f_you: {
      say: ["Full transparency: GoMining pays me a referral commission plus ongoing ambassador rewards tied to your mining fees.", "That's why I want you set up right."],
      opts: [{ t: "Another question", go: "faq" }, { t: "Let's get started", go: "signup" }]
    },
    f_newbie: {
      say: ["You can still do this. Plenty of the people I sign up are new to crypto.", "Run your numbers in the calculator, make your own call, and if you get stuck anywhere, message me. I answer myself."],
      links: [{ t: "Open the calculator", href: "/#calculator" }],
      opts: [{ t: "Another question", go: "faq" }, { t: "Let's get started", go: "signup" }]
    },
    stuck: {
      say: ["No problem. Where are you stuck?"],
      opts: [
        { t: "Can't find my GoMining ID", go: "h_id" },
        { t: "Something else", go: "human" }
      ]
    },
    h_id: {
      say: ["In GoMining, tap your profile picture in the top right. Your ID is the number right under your name."],
      img: { src: "/assets/img/find-your-id.png", alt: "GoMining profile card with the ID number under the name highlighted" },
      opts: [{ t: "Found it ✓", go: "buy" }, { t: "Still stuck", go: "human" }]
    },
    human: {
      say: ["Hit me up directly. I answer myself, usually pretty quick."],
      links: [{ t: "Message me on Telegram", kind: "tg" }, { t: "Text me", kind: "sms" }],
      opts: [{ t: "Back to the start", go: "start" }]
    }
  };

  // ---------- state ----------
  var state = load() || { node: null, data: {} };
  function load() { try { return JSON.parse(sessionStorage.getItem(STORE)); } catch (e) { return null; } }
  function save() { try { sessionStorage.setItem(STORE, JSON.stringify(state)); } catch (e) {} }

  // ---------- styles ----------
  var css = document.createElement("link");
  css.rel = "stylesheet"; css.href = "/assets/css/copilot.css?v=202610262200";
  document.head.appendChild(css);

  // ---------- shell ----------
  var fab = el("button", "rc-fab");
  fab.type = "button";
  fab.setAttribute("aria-label", "Open the setup guide");
  fab.setAttribute("aria-expanded", "false");
  fab.innerHTML = '<img src="/assets/brand/ringomining-profile-pic.png" alt="" width="56" height="56"><span class="rc-dot"></span>';

  var tip = el("div", "rc-tip");
  tip.innerHTML = '<button type="button" class="rc-tip-x" aria-label="Dismiss">×</button><span>Need a hand? I\'ll walk you through it.</span>';

  var panel = el("section", "rc-panel");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Ringo's setup guide");
  panel.hidden = true;
  panel.innerHTML =
    '<header class="rc-head"><img src="/assets/brand/ringomining-profile-pic.png" alt="" width="36" height="36">' +
    '<div><strong>Ringo</strong><small>Setup guide · code ' + CODE + '</small></div>' +
    '<button type="button" class="rc-restart" title="Start over">↺</button>' +
    '<button type="button" class="rc-close" aria-label="Close">×</button></header>' +
    '<div class="rc-log" aria-live="polite"></div>' +
    '<div class="rc-foot"></div>';
  var log = panel.querySelector(".rc-log");
  var foot = panel.querySelector(".rc-foot");

  document.body.appendChild(panel);
  document.body.appendChild(tip);
  document.body.appendChild(fab);

  var seenTip = false;
  try { seenTip = !!localStorage.getItem(STORE + "_tip"); } catch (e) {}
  if (!seenTip && !state.node) setTimeout(function () { if (panel.hidden) tip.classList.add("show"); }, 6000);
  function hideTip() { tip.classList.remove("show"); try { localStorage.setItem(STORE + "_tip", "1"); } catch (e) {} }
  tip.querySelector(".rc-tip-x").addEventListener("click", hideTip);
  tip.querySelector("span").addEventListener("click", function () { hideTip(); open(); });

  fab.addEventListener("click", function () { panel.hidden ? open() : close(); });
  panel.querySelector(".rc-close").addEventListener("click", close);
  panel.querySelector(".rc-restart").addEventListener("click", function () {
    state = { node: null, data: {} }; save(); log.innerHTML = ""; go("start");
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) close(); });

  // other pages can open the guide: <a data-copilot href="#">Walk me through it</a>
  document.querySelectorAll("[data-copilot]").forEach(function (a) {
    a.addEventListener("click", function (e) { e.preventDefault(); open(); });
  });

  var started = false;
  function open() {
    hideTip();
    panel.hidden = false;
    fab.classList.add("on");
    fab.setAttribute("aria-expanded", "true");
    document.documentElement.classList.add("rc-open");
    if (!started) {
      started = true;
      if (state.node && NODES[state.node] && state.node !== "start") {
        bot(["Picking up where we left off."], true);
        go(state.node, true);
      } else go("start");
    }
    var f = foot.querySelector("input, button");
    if (f) setTimeout(function () { f.focus(); }, 50);
  }
  function close() {
    panel.hidden = true;
    fab.classList.remove("on");
    fab.setAttribute("aria-expanded", "false");
    document.documentElement.classList.remove("rc-open");
    fab.focus();
  }

  // ---------- rendering ----------
  function el(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }
  function scroll() { log.scrollTop = log.scrollHeight; }

  function bot(lines, instant, after) {
    var i = 0;
    var reduce = instant || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
    (function next() {
      if (i >= lines.length) { if (after) after(); return; }
      var b = el("div", "rc-msg rc-bot");
      if (reduce) {
        b.innerHTML = lines[i++]; log.appendChild(b); scroll(); next();
      } else {
        b.classList.add("rc-typing"); b.innerHTML = "<i></i><i></i><i></i>";
        log.appendChild(b); scroll();
        setTimeout(function () { b.classList.remove("rc-typing"); b.innerHTML = lines[i++]; scroll(); next(); },
          Math.min(900, 280 + lines[i].length * 6));
      }
    })();
  }

  function me(text) {
    var b = el("div", "rc-msg rc-me");
    b.textContent = text; // user text is never treated as HTML
    log.appendChild(b); scroll();
  }

  function linkBtn(l) {
    if (l.kind === "copy") {
      var c = el("button", "rc-link"); c.type = "button"; c.textContent = l.t;
      c.addEventListener("click", function () { copyCode(c); });
      return c;
    }
    var a = el("a", "rc-link" + (l.kind === "ref" ? " rc-fire" : ""));
    a.textContent = l.t;
    a.href = l.kind === "ref" ? REF : l.kind === "tg" ? TG : l.kind === "sms" ? SMS : l.href;
    if (l.kind === "ref" || l.kind === "tg" || /^https?:/.test(l.href || "")) { a.target = "_blank"; a.rel = "noopener"; }
    if (l.href && l.href.charAt(0) === "/" && l.href.indexOf("#") > -1 && location.pathname === "/") {
      a.addEventListener("click", function () { close(); });
    }
    return a;
  }

  function copyCode(btn) {
    var done = function () { var o = btn.textContent; btn.textContent = "Copied ✓"; setTimeout(function () { btn.textContent = o; }, 1500); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(CODE).then(done, done);
    else done();
  }

  function go(id, instant) {
    var n = NODES[id];
    if (!n) return;
    state.node = id; save();
    foot.innerHTML = "";
    if (n.review) return review();
    bot(n.say, instant, function () {
      if (n.img) {
        var w = el("div", "rc-msg rc-bot rc-img");
        var im = el("img"); im.src = n.img.src; im.alt = n.img.alt; im.loading = "lazy";
        w.appendChild(im); log.appendChild(w);
        im.addEventListener("load", scroll);
      }
      if (n.links) {
        var row = el("div", "rc-links");
        n.links.forEach(function (l) { row.appendChild(linkBtn(l)); });
        log.appendChild(row);
      }
      scroll();
      if (n.ask) return ask(n.ask);
      if (n.opts) options(n.opts);
    });
  }

  function options(opts) {
    foot.innerHTML = "";
    var wrap = el("div", "rc-opts");
    opts.forEach(function (o) {
      var b = el("button", "rc-opt"); b.type = "button"; b.textContent = o.t;
      b.addEventListener("click", function () { me(o.t); go(o.go); });
      wrap.appendChild(b);
    });
    foot.appendChild(wrap);
    scroll();
  }

  function ask(q) {
    foot.innerHTML = "";
    var f = el("form", "rc-ask"); f.noValidate = true;
    var inp = el("input");
    inp.type = q.type === "email" ? "email" : "text";
    inp.setAttribute("aria-label", q.label);
    inp.placeholder = q.label + (q.optional ? " (optional)" : "");
    inp.autocomplete = q.type === "email" ? "email" : "off";
    inp.spellcheck = false;
    if (q.mode) inp.inputMode = q.mode;
    if (state.data[q.key]) inp.value = state.data[q.key];
    var send = el("button", "rc-send"); send.type = "submit"; send.textContent = "Send";
    f.appendChild(inp); f.appendChild(send);
    if (q.optional) {
      var skip = el("button", "rc-skip"); skip.type = "button"; skip.textContent = "Skip";
      skip.addEventListener("click", function () { state.data[q.key] = ""; me("Skip"); go(q.next); });
      f.appendChild(skip);
    }
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var v = inp.value.trim();
      if (q.key === "tg" && v && v.charAt(0) !== "@") v = "@" + v;
      if (!v && q.optional) { state.data[q.key] = ""; me("Skip"); return go(q.next); }
      if (q.check && !q.check.test(v)) { inp.setCustomValidity(q.bad); bot([q.bad], true); inp.focus(); return; }
      state.data[q.key] = v; save();
      me(v);
      go(q.next);
    });
    inp.addEventListener("input", function () { inp.setCustomValidity(""); });
    foot.appendChild(f);
    scroll();
    setTimeout(function () { inp.focus(); }, 30);
  }

  function review() {
    var d = state.data;
    bot(["Here's what I've got. Look right?"], false, function () {
      var card = el("div", "rc-msg rc-bot rc-card");
      [["Email", d.email], ["GoMining ID", d.gmid], ["Telegram", d.tg || "none"]].forEach(function (r) {
        var row = el("div"); var k = el("span"); var v = el("b");
        k.textContent = r[0]; v.textContent = r[1] || "";
        row.appendChild(k); row.appendChild(v); card.appendChild(row);
      });
      log.appendChild(card);
      var fine = el("div", "rc-fine");
      fine.innerHTML = 'By sending, you agree to the <a href="/disclaimer">disclaimer</a>. I\'m an independent ambassador, not affiliated with GoMining. Not financial advice.';
      log.appendChild(fine);
      scroll();
      var wrap = el("div", "rc-opts");
      var s = el("button", "rc-opt rc-primary"); s.type = "button"; s.textContent = "Send it →";
      s.addEventListener("click", function () { submit(s); });
      var fix = el("button", "rc-opt"); fix.type = "button"; fix.textContent = "Fix something";
      fix.addEventListener("click", function () { me("Fix something"); go("q_email"); });
      wrap.appendChild(s); wrap.appendChild(fix);
      foot.innerHTML = ""; foot.appendChild(wrap);
    });
  }

  function submit(btn) {
    var d = state.data;
    if (!d.email || !d.gmid) { bot(["I'm missing something. Let's run through it again."], true); return go("q_email"); }
    var id = C.FORM_STEP3_ID, map = C.ENTRIES_STEP3 || {};
    if (!id || !map.email) { me("Send it"); return go("sendfail"); }
    btn.disabled = true; btn.textContent = "Sending…";
    var body = new URLSearchParams();
    var vals = {
      email: d.email, gmid: d.gmid, wallet: "Not collected",
      coin: "GoMining token (GMT)", chain: "BNB Smart Chain (BEP-20)",
      notes: (d.tg ? d.tg + "\n" : "") + "Sent from the co-pilot chat"
    };
    Object.keys(vals).forEach(function (k) { if (map[k] && vals[k]) body.append("entry." + map[k], vals[k]); });
    fetch("https://docs.google.com/forms/d/e/" + id + "/formResponse", { method: "POST", mode: "no-cors", body: body })
      .then(function () {
        try { localStorage.setItem("ringo_STEP3", "1"); } catch (e) {}
        me("Send it");
        state.data = {}; go("sent");
      })
      .catch(function () { me("Send it"); go("sendfail"); });
  }
})();
