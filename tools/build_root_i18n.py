#!/usr/bin/env python3
"""
Translates the hand-written root pages (index, apply, signup, verify, disclaimer)
into /fr/, /es/, /de/ and adds language links (hreflang + language menu) to every
version, including the English originals.

How it works: the English page is split into "text units" (any element that holds
text directly, taken with its inner HTML) plus translatable attributes (alt,
placeholder, title, aria-label, meta descriptions). Each unit is looked up in
tools/i18n_root/<lang>.py (a dict named T). Form values (value="...") are never touched, so the
Google Forms still receive the English answers they expect.

  python3 tools/build_root_i18n.py --extract   # list English units -> tools/i18n_root/en_units.json
  python3 tools/build_root_i18n.py             # build all languages (fails on any missing translation)
"""
import html as htmlmod
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n import HUB_SLUG, LANG_NAMES, LANG_ORDER, LOCALES, PREFIX, UI  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TR = ROOT / "tools" / "i18n_root"
DOMAIN = "https://ringomining.com"
FILES = ["index.html", "apply.html", "signup.html", "verify.html", "disclaimer.html"]
ROOT_KEY = {"index.html": "/", "apply.html": "/apply.html", "signup.html": "/signup.html",
            "verify.html": "/verify.html", "disclaimer.html": "/disclaimer.html"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ATTRS = ("alt", "placeholder", "title", "aria-label")
KEEP = {"RINGOMINING", "CODE: RINGO5 ⧉", "Ethereum · Uniswap V2", "150B SHIB", "292.54 ETH", "ringoshi.eth",
        "0xfc688c…13e6c2", "GoMining", "GMT Optimizer", "USDC", "Bitcoin", "Telegram", "RINGO5", "@yourname"}
META_TR = re.compile(r'(<meta\s+(?:name|property)="(?:description|og:title|og:description|og:image:alt|twitter:title|twitter:description)"\s+content=")([^"]*)(")')
TOKEN = re.compile(r"<!--.*?-->|<(/?)([a-zA-Z0-9]+)([^>]*)>|([^<]+)", re.S)


def has_letters(s):
    return re.search(r"[A-Za-z]", re.sub(r"<[^>]+>", "", s)) is not None


def units(doc):
    """Return [(start, end, inner_html)] for text-bearing elements outside <head> scripts/styles."""
    out, stack = [], []  # stack entries: [tag, inner_start, has_text, in_unit_depth]
    skip = None
    for m in TOKEN.finditer(doc):
        closing, tag, rest, text = m.group(1), m.group(2), m.group(3), m.group(4)
        if skip:
            if tag and closing and tag.lower() == skip:
                skip = None
            continue
        if text is not None:
            if text.strip() and stack:
                stack[-1][2] = True
            continue
        if tag is None:
            continue  # comment
        t = tag.lower()
        if not closing:
            if t in ("script", "style"):
                skip = t
                continue
            if t in VOID or rest.rstrip().endswith("/"):
                continue
            stack.append([t, m.end(), False])
        else:
            # pop to matching tag
            while stack and stack[-1][0] != t:
                stack.pop()
            if not stack:
                continue
            tg, start, has_text = stack.pop()
            if has_text:
                out.append((start, m.start(), doc[start:m.start()]))
            if has_text and stack:
                pass
    # keep only outermost units (drop units nested inside another unit)
    out.sort()
    keep, last_end = [], -1
    for s, e, inner in sorted(out, key=lambda x: (x[0], -(x[1]))):
        if s >= last_end:
            keep.append((s, e, inner))
            last_end = e
    return keep


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def attr_units(doc):
    found = []
    for m in re.finditer(r"<[a-zA-Z][^>]*>", doc):
        for a in ATTRS:
            for am in re.finditer(r'\s%s="([^"]*)"' % a, m.group(0)):
                if has_letters(am.group(1)):
                    found.append(am.group(1))
    found += [m.group(2) for m in META_TR.finditer(doc)]
    return found


def body_of(doc):
    return doc  # whole document; <head> has only <title> as a text unit


def extract():
    res = {}
    for f in FILES:
        doc = strip_i18n((ROOT / f).read_text())
        seen = []
        for _, _, inner in units(doc):
            k = norm(inner)
            if has_letters(k) and k not in seen:
                seen.append(k)
        for a in attr_units(doc):
            k = norm(htmlmod.unescape(a))
            if k not in seen:
                seen.append(k)
        res[f] = seen
    (TR / "en_units.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("wrote", sum(len(v) for v in res.values()), "units")


# ---------------------------------------------------------------- language chrome
def strip_i18n(doc):
    doc = re.sub(r"\n?\s*<!--i18n-->.*?<!--/i18n-->", "", doc, flags=re.S)
    doc = re.sub(r"<!--LM-->.*?<!--/LM-->", "", doc, flags=re.S)
    doc = re.sub(r'\n?<script src="[^"]*lang\.js[^"]*"></script>', "", doc)
    return doc


def lang_menu(lang, alts):
    items = "".join(
        f'<a href="{alts[l]}" hreflang="{l}" lang="{l}" data-setlang="{l}"' + (' aria-current="true"' if l == lang else "") + f'>{LANG_NAMES[l]}</a>'
        for l in LANG_ORDER if l in alts)
    return (f'<!--LM--><details class="lang-menu"><summary aria-label="{UI[lang]["lang_label"]}">{lang.upper()} ▾</summary>'
            f'<div class="menu">{items}</div></details><!--/LM-->')


def add_chrome(doc, lang, alts, prefix_assets):
    links = "\n".join(f'  <link rel="alternate" hreflang="{l}" href="{DOMAIN}{alts[l]}">' for l in LANG_ORDER if l in alts)
    links += f'\n  <link rel="alternate" hreflang="x-default" href="{DOMAIN}{alts["en"]}">'
    loc = f'\n  <meta property="og:locale" content="{LOCALES[lang]}">'
    doc = doc.replace('  <meta name="theme-color"', f"  <!--i18n-->\n{links}{loc}\n  <!--/i18n-->\n  <meta name=\"theme-color\"", 1)
    menu = lang_menu(lang, alts)
    hdr_end = doc.find("</header>")
    hdr = doc[:hdr_end]
    if '<nav class="bar-nav">' in hdr:
        i = hdr.find("<button class=\"code-pill\"")
        hdr = hdr[:i] + menu + hdr[i:]
    else:
        hdr = re.sub(r'(<button class="code-pill"[^>]*>.*?</button>)', '<nav class="bar-nav">' + menu + r'\1</nav>', hdr, count=1, flags=re.S)
    doc = hdr + doc[hdr_end:]
    src = ("/assets/js/lang.js" if prefix_assets else "assets/js/lang.js")
    doc = doc.replace("</body>", f'<script src="{src}"></script>\n</body>', 1)
    return doc


# ---------------------------------------------------------------- translate
def guide_slug_map():
    m = {}
    for lang in LANG_ORDER:
        for f in sorted((ROOT / "tools" / "pages" / lang).glob("*.html")):
            meta = json.loads(re.match(r"\s*<!--META\s*(\{.*?\})\s*-->", f.read_text(), re.S).group(1))
            m.setdefault(meta["order"], {})[lang] = meta["slug"]
    return m


def translate(doc, table, lang, fname, missing):
    def tr(s):
        k = norm(s)
        if k in KEEP:
            return s
        if k in table:
            return table[k]
        missing.append(k)
        return s

    us = units(doc)
    for s, e, inner in reversed(us):
        if not has_letters(inner):
            continue
        doc = doc[:s] + tr(inner) + doc[e:]

    def tr_attr(m):
        tag = m.group(0)
        for a in ATTRS:
            tag = re.sub(r'(\s%s=")([^"]*)(")' % a,
                         lambda am: am.group(1) + (htmlmod.escape(tr(htmlmod.unescape(am.group(2))), quote=True) if has_letters(am.group(2)) else am.group(2)) + am.group(3), tag)
        return tag
    doc = re.sub(r"<[a-zA-Z][^>]*>", tr_attr, doc)
    doc = META_TR.sub(lambda m: m.group(1) + htmlmod.escape(tr(htmlmod.unescape(m.group(2))), quote=True) + m.group(3), doc)
    return doc


def localize_links(doc, lang, slug_map):
    P = PREFIX[lang]
    doc = doc.replace('<html lang="en">', f'<html lang="{lang}">', 1)
    # assets to absolute paths
    doc = re.sub(r'(href|src)="(assets/|favicon\.ico|site\.webmanifest)', r'\1="/\2', doc)
    doc = doc.replace("url(\"../img/", "url(\"/assets/img/")
    # root pages
    for f in ["apply.html", "signup.html", "verify.html", "disclaimer.html"]:
        doc = re.sub(r'href="/?%s' % re.escape(f), f'href="{P}/{f}', doc)
    doc = re.sub(r'href="/?index\.html', f'href="{P}/', doc)
    doc = doc.replace('href="/#', f'href="{P}/#').replace('href="/"', f'href="{P}/"')
    doc = doc.replace('href="/guides/"', f'href="{P}/{HUB_SLUG[lang]}/"')
    for order, slugs in slug_map.items():
        if lang in slugs:
            doc = doc.replace(f'href="/{slugs["en"]}/"', f'href="{P}/{slugs[lang]}/"')
    # canonical / og:url
    doc = re.sub(r'(<link rel="canonical" href="https://ringomining\.com)(/[^"]*)"', lambda m: f'{m.group(1)}{P}{m.group(2)}"', doc)
    doc = re.sub(r'(<meta property="og:url" content="https://ringomining\.com)(/[^"]*)"', lambda m: f'{m.group(1)}{P}{m.group(2)}"', doc)
    og = f"/assets/img/og/{lang}/home.jpg"
    if (ROOT / og.lstrip("/")).exists():
        doc = doc.replace("https://ringomining.com/assets/img/og-image.jpg", DOMAIN + og)
    return doc


def faq_ld(doc, lang):
    """Rebuild JSON-LD on the translated homepage from its translated FAQ."""
    doc = re.sub(r'\s*<script type="application/ld\+json">.*?</script>', "", doc, flags=re.S)
    qa = re.findall(r"<summary>(.*?)</summary>\s*<p>(.*?)</p>", doc, re.S)
    clean = lambda s: htmlmod.unescape(re.sub(r"<[^>]+>", "", s)).strip()
    ld = [
        {"@context": "https://schema.org", "@type": "WebSite", "name": "RinGoMining", "url": f"{DOMAIN}{PREFIX[lang]}/", "inLanguage": lang},
        {"@context": "https://schema.org", "@type": "Person", "name": "Ringo", "alternateName": "RinGoMining", "url": f"{DOMAIN}/",
         "sameAs": ["https://t.me/RingoShitoitsu"]},
    ]
    if qa:
        ld.append({"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": lang, "mainEntity": [
            {"@type": "Question", "name": clean(q), "acceptedAnswer": {"@type": "Answer", "text": clean(a)}} for q, a in qa]})
    block = "\n".join('  <script type="application/ld+json">' + json.dumps(x, ensure_ascii=False) + "</script>" for x in ld)
    return doc.replace("</head>", block + "\n</head>", 1)


def main():
    if "--extract" in sys.argv:
        extract()
        return
    slug_map = guide_slug_map()
    langs = [l for l in LANG_ORDER if l != "en" and (TR / f"{l}.py").exists()]
    alts_all = {f: {l: (PREFIX[l] + ROOT_KEY[f]) for l in ["en"] + langs} for f in FILES}
    missing_all = {}
    for f in FILES:
        en_doc = strip_i18n((ROOT / f).read_text())
        for lang in langs:
            ns = {}
            exec((TR / f"{lang}.py").read_text(), ns)
            table = {norm(k): v for k, v in ns["T"].items()}
            missing = []
            doc = translate(en_doc, table, lang, f, missing)
            if missing:
                missing_all.setdefault(lang, []).extend(x for x in missing if x not in missing_all.get(lang, []))
                continue
            doc = localize_links(doc, lang, slug_map)
            if f == "index.html":
                doc = faq_ld(doc, lang)
            doc = add_chrome(doc, lang, alts_all[f], True)
            out = ROOT / lang / f
            out.parent.mkdir(exist_ok=True)
            out.write_text(doc)
            print("built", out.relative_to(ROOT))
        # English original gets the language links too
        (ROOT / f).write_text(add_chrome(en_doc, "en", alts_all[f], False))
    if missing_all:
        for l, ms in missing_all.items():
            print(f"\n[{l}] {len(ms)} missing translations:")
            for m in ms:
                print("  ", m[:160])
        sys.exit(1)


if __name__ == "__main__":
    main()
