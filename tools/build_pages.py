#!/usr/bin/env python3
"""
Builds the RinGoMining guide pages in every language from tools/pages/<lang>/*.html.

Each page file starts with a JSON front-matter block between <!--META and -->,
followed by the article body HTML. "order" ties the translations of one page together.
The body can use these shared blocks (text lives in tools/i18n.py):

  {{CODE_HINT}}  soft first CTA line           {{OFFER}}     bonus cards + button
  {{OPTIMIZER}}  GMT Optimizer embed           {{COMPARE}}   returns comparison + live APR
  {{APR}}        live staking APR (inline)     {{APR_DATE}}  date the APR was updated
  {{VIDEOS}}     the four VoskCoin farm tours  {{HABIT}}     $25-$50/week habit block
  {{STORY}}      short version of Ringo's story
  {{STEPS}}      the 3-step funnel with button {{CLOSE}}     final call to action
  {{P}}          language prefix for links ("" for English, "/fr" for French, ...)

Run:  python3 tools/build_pages.py
Also rebuilds the guides hub for each language, the language links on the
hand-written root pages (see tools/build_root_i18n.py), and sitemap.xml.
"""
import html
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n import (APR, APR_DATE, B, HUB_SLUG, LANG_NAMES, LANG_ORDER, LOCALES, PREFIX,  # noqa: E402
                  ROOT_PAGES, SRC_LINKS, UI, VIDEOS, fmt_date)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "pages"
DOMAIN = "https://ringomining.com"
VER = time.strftime("%Y%m%d%H%M")
FONTS = ("https://fonts.googleapis.com/css2?family=Anton&family=Outfit:wght@600;700"
         "&family=JetBrains+Mono:wght@400;700&family=Space+Grotesk:wght@400;500;600;700&display=swap")


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- blocks
def blocks(lang):
    t = B[lang]
    P = PREFIX[lang]
    apr_date = APR_DATE[lang]
    vids = []
    for vid, title, place in VIDEOS:
        vids.append(f"""
    <figure class="yt">
      <button class="yt-lite" type="button" data-yt="{vid}" aria-label="{esc(t['vid_play'])}: {esc(title)}">
        <img src="https://i.ytimg.com/vi/{vid}/hqdefault.jpg" alt="" loading="lazy" width="480" height="360">
        <span class="yt-play" aria-hidden="true"></span>
      </button>
      <figcaption><b>{esc(place[lang])}</b> · VoskCoin: “{esc(title)}”</figcaption>
    </figure>""")
    return {
        "P": P,
        "APR": APR,
        "APR_DATE": apr_date,
        "CODE_HINT": f"""
<aside class="callout callout-code">
  <div>
    <strong>{t['hint_strong']}</strong>
    <span>{t['hint_span']}</span>
  </div>
  <a class="btn btn-fire" href="{P}/apply">{t['hint_btn']}</a>
</aside>""",
        "OFFER": f"""
<section class="block offer-block" id="bonus">
  <p class="kicker">{t['offer_kicker']}</p>
  <h2>{t['offer_h2']}</h2>
  <p>{t['offer_p']}</p>
  <div class="offer">
    <div class="card"><div class="big">{t['c1_big']}</div><h3>{t['c1_h']}</h3><p>{t['c1_p']}</p></div>
    <div class="card"><div class="big">10%</div><h3>{t['c2_h']}</h3><p>{t['c2_p']}</p></div>
    <div class="card"><div class="big">5%</div><h3>{t['c3_h']}</h3><p>{t['c3_p']}</p></div>
    <div class="card"><div class="big">{t['c4_big']}</div><h3>{t['c4_h']}</h3><p>{t['c4_p']}</p></div>
  </div>
  <p class="fine">{t['offer_fine']} <a href="{P}/disclaimer">{t['terms']}</a>.</p>
  <a class="btn btn-fire" href="{P}/apply">{t['offer_btn']}</a>
</section>""",
        "OPTIMIZER": f"""
<section class="block optimizer-block" id="calculator">
  <p class="kicker">GMT Optimizer</p>
  <h2>{t['opt_h2']}</h2>
  <p>{t['opt_p']}</p>
  <div class="optimizer-box"><div data-gmt-optimizer data-src="funnel"></div></div>
  <p class="fine">{t['opt_fine']}</p>
</section>""",
        "COMPARE": f"""
<section class="block compare-block">
  <p class="kicker">{t['cmp_kicker']}</p>
  <h2>{t['cmp_h2']}</h2>
  <div class="rows">
    <div class="row"><span class="what">{t['r_sav']}</span><span class="val muted">{t['r_sav_v']}</span></div>
    <div class="row"><span class="what">{t['r_usdc']}</span><span class="val">{t['r_usdc_v']}<sup><a href="#src-usdc">1</a></sup></span></div>
    <div class="row"><span class="what">{t['r_sp']}</span><span class="val">{t['r_sp_v']}<sup><a href="#src-sp">2</a></sup></span></div>
    <div class="row"><span class="what">{t['r_home']}</span><span class="val bad">{t['r_home_v']}</span></div>
    <div class="row hi"><span class="what">{t['r_stake']}</span><span class="val">{t['r_stake_v'].replace('{APR}', APR)}</span></div>
  </div>
  <p class="fine">{t['cmp_fine'].replace('{APR_DATE}', apr_date)}</p>
  <ol class="sources">
    <li id="src-usdc">{t['src_usdc'].replace('{usdc}', SRC_LINKS['usdc'])}</li>
    <li id="src-sp">{t['src_sp'].replace('{sp}', SRC_LINKS['sp'])}</li>
  </ol>
</section>""",
        "HABIT": f"""
<section class="block habit-block">
  <p class="kicker">{t['hab_kicker']}</p>
  <h2>{t['hab_h2']}</h2>
  <div class="habit">
    <div><b>{t['hab1_b']}</b><span>{t['hab1_s']}</span><em>{t['hab1_e']}</em></div>
    <div><b>{t['hab2_b']}</b><span>{t['hab2_s']}</span><em>{t['hab2_e']}</em></div>
    <div class="hl"><b>{t['hab3_b']}</b><span>{t['hab3_s']}</span><em>{t['hab3_e']}</em></div>
  </div>
  <p>{t['hab_p']}</p>
  <a class="btn btn-ghost" href="#calculator">{t['hab_btn']}</a>
</section>""",
        "STORY": f"""
<aside class="story-box">
  <p class="kicker">{t['story_kicker']}</p>
  <p>{t['story_p1']}</p>
  <p>{t['story_p2']} <a href="{P}/#story">{t['story_link']}</a></p>
</aside>""",
        "STEPS": f"""
<section class="block steps-block">
  <p class="kicker">{t['st_kicker']}</p>
  <h2>{t['st_h2']}</h2>
  <ol class="mini-steps">
    <li><b>{t['st1_b']}</b><span>{t['st1_s']}</span></li>
    <li><b>{t['st2_b']}</b><span>{t['st2_s']}</span></li>
    <li><b>{t['st3_b']}</b><span>{t['st3_s']}</span></li>
  </ol>
  <a class="btn btn-fire" href="{P}/apply">{t['st_btn']}</a>
</section>""",
        "CLOSE": f"""
<section class="close-band">
  <h2>{t['cl_h2']}</h2>
  <p>{t['cl_p']}</p>
  <a class="btn btn-fire" href="{P}/apply">{t['cl_btn']}</a>
</section>""",
        "VIDEOS": f"""
<section class="block videos-block">
  <div class="yt-grid">{''.join(vids)}
  </div>
  <p class="fine">{t['vid_fine']}</p>
</section>""",
    }


# ---------------------------------------------------------------- shared chrome
def alt_links(alts):
    """alts: {lang: absolute path}. Returns hreflang <link> tags."""
    out = [f'  <link rel="alternate" hreflang="{l}" href="{DOMAIN}{alts[l]}">' for l in LANG_ORDER if l in alts]
    if "en" in alts:
        out.append(f'  <link rel="alternate" hreflang="x-default" href="{DOMAIN}{alts["en"]}">')
    return "\n".join(out)


def lang_menu(lang, alts):
    items = "".join(
        f'<a href="{alts[l]}" hreflang="{l}" lang="{l}" data-setlang="{l}"' + (' aria-current="true"' if l == lang else "") + f'>{LANG_NAMES[l]}</a>'
        for l in LANG_ORDER if l in alts)
    return (f'<details class="lang-menu"><summary aria-label="{esc(UI[lang]["lang_label"])}">{lang.upper()} ▾</summary>'
            f'<div class="menu">{items}</div></details>')


def head(meta, url, extra_ld, lang, alts, kind="article"):
    t, d = esc(meta["title"]), esc(meta["description"])
    own = f"/assets/img/og/{lang}/{meta.get('og', '')}.jpg"
    img = DOMAIN + (own if (ROOT / own.lstrip("/")).exists() else "/assets/img/og-image.jpg")
    P = PREFIX[lang]
    ld = [{
        "@context": "https://schema.org", "@type": "Article" if kind == "article" else "CollectionPage",
        "headline": meta["h1_plain"], "description": meta["description"], "inLanguage": lang,
        "image": img, "datePublished": meta["published"], "dateModified": meta["updated"],
        "author": {"@type": "Person", "name": "Ringo", "url": DOMAIN + "/"},
        "publisher": {"@type": "Organization", "name": "RinGoMining", "url": DOMAIN + "/",
                      "logo": {"@type": "ImageObject", "url": DOMAIN + "/assets/icons/icon-512.png"}},
        "mainEntityOfPage": url,
    }, {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": UI[lang]["crumb_home"], "item": f"{DOMAIN}{P}/"},
            {"@type": "ListItem", "position": 2, "name": UI[lang]["crumb_guides"], "item": f"{DOMAIN}{P}/{HUB_SLUG[lang]}/"},
        ] + ([{"@type": "ListItem", "position": 3, "name": meta["short"], "item": url}] if kind == "article" else []),
    }] + extra_ld
    ld_html = "\n".join('  <script type="application/ld+json">' + json.dumps(x, ensure_ascii=False) + "</script>" for x in ld)
    og_alt = "\n".join(f'  <meta property="og:locale:alternate" content="{LOCALES[l]}">' for l in alts if l != lang)
    return f"""<!doctype html>
<html lang="{lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{t}</title>
  <meta name="description" content="{d}">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <link rel="canonical" href="{url}">
{alt_links(alts)}
  <meta property="og:type" content="{'article' if kind == 'article' else 'website'}">
  <meta property="og:site_name" content="RinGoMining">
  <meta property="og:locale" content="{LOCALES[lang]}">
{og_alt}
  <meta property="og:url" content="{url}">
  <meta property="og:title" content="{t}">
  <meta property="og:description" content="{d}">
  <meta property="og:image" content="{img}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="{t}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{t}">
  <meta name="twitter:description" content="{d}">
  <meta name="twitter:image" content="{img}">
  <meta property="article:published_time" content="{meta['published']}">
  <meta property="article:modified_time" content="{meta['updated']}">
  <meta name="theme-color" content="#0b0907">
  <link rel="icon" href="/favicon.ico?v={VER}" sizes="32x32">
  <link rel="icon" href="/assets/icons/icon.svg?v={VER}" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/assets/icons/apple-touch-icon.png">
  <link rel="manifest" href="/site.webmanifest">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="{FONTS}" rel="stylesheet">
  <link rel="stylesheet" href="/assets/css/style.css?v={VER}">
  <link rel="stylesheet" href="/assets/css/article.css?v={VER}">
{ld_html}
</head>
<body class="article-page">
"""


def header(lang, alts):
    P, u = PREFIX[lang], UI[lang]
    return f"""<header class="bar">
  <div class="wrap">
    <a class="brand" href="{P}/"><img class="brand-mark" src="/assets/icons/icon.svg" alt="" width="30" height="30"><span>RINGOMINING</span></a>
    <nav class="bar-nav"><a class="nav-guides" href="{P}/{HUB_SLUG[lang]}/">{u['nav_guides']}</a>{lang_menu(lang, alts)}<button class="code-pill" data-copy="RINGO5" title="{esc(u['copy_title'])}">CODE: RINGO5 ⧉</button></nav>
  </div>
</header>
"""


def footer(lang):
    P, u = PREFIX[lang], UI[lang]
    return f"""
<footer>
  <div class="wrap">
    <nav class="foot-links"><a href="{P}/">{u['foot_home']}</a><a href="{P}/{HUB_SLUG[lang]}/">{u['foot_guides']}</a><a href="{P}/apply">{u['foot_claim']}</a><a href="{P}/disclaimer">{u['foot_disclaimer']}</a></nav>
    <div class="contact-row">
      <span>{u['tg']}: <a data-tg href="https://t.me/RinGoMining" target="_blank" rel="noopener">@RinGoMining</a></span>
      <span>{u['phone']}: <a data-phone>906-235-5711</a></span>
      <span>{u['refcode']}: <strong style="color:var(--gold)">RINGO5</strong></span>
    </div>
    <p style="margin:0">{u['foot_p']} <a href="{P}/disclaimer">{u['read_disclaimer']}</a>.</p>
  </div>
</footer>

<div class="sticky-cta"><a class="btn btn-fire btn-block" href="{P}/apply">{u['sticky']}</a></div>

<script src="/assets/js/config.js?v={VER}"></script>
<script src="/assets/js/main.js?v={VER}"></script>
<script src="/assets/js/article.js?v={VER}"></script>
<script src="/assets/js/lang.js?v={VER}"></script>
<script src="https://gmt-optimizer.com/assets/rates.js" async></script>
<script src="https://gmt-optimizer.com/assets/embed.js" async></script>
</body>
</html>
"""


# ---------------------------------------------------------------- pages
def page_path(lang, slug):
    return f"{PREFIX[lang]}/{slug}/"


def build_page(lang, meta, pages, alts):
    url = DOMAIN + page_path(lang, meta["slug"])
    body = meta["_body"]
    for k, v in blocks(lang).items():
        body = body.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", body)
    if left:
        raise SystemExit(f"{lang}/{meta['slug']}: unknown blocks {left}")
    u = UI[lang]
    P = PREFIX[lang]

    faq_html, extra_ld = "", []
    if meta.get("faq"):
        items = "".join(f"<details><summary>{esc(q)}</summary><p>{a}</p></details>" for q, a in meta["faq"])
        faq_html = f'<section class="block faq"><p class="kicker">{u["faq_kicker"]}</p><h2>{u["faq_h2"]}</h2>{items}</section>'
        extra_ld.append({"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": lang, "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": re.sub(r"<[^>]+>", "", a)}}
            for q, a in meta["faq"]]})

    related = [p for p in pages if p["slug"] != meta["slug"]]
    rel_html = "".join(f'<a class="rel" href="{page_path(lang, p["slug"])}"><b>{esc(p["short"])}</b><span>{esc(p["blurb"])}</span></a>' for p in related)

    upd = fmt_date(meta["updated"], lang)
    out_html = head(meta, url, extra_ld, lang, alts) + header(lang, alts) + f"""
<main class="article">
  <div class="wrap narrow">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="{P}/">{u['crumb_home']}</a><span>›</span><a href="{P}/{HUB_SLUG[lang]}/">{u['crumb_guides']}</a><span>›</span><span>{esc(meta['short'])}</span></nav>
    <p class="eyebrow">{esc(meta['eyebrow'])}</p>
    <h1>{meta['h1']}</h1>
    <p class="lede">{meta['lede']}</p>
    <div class="byline">
      <img src="/assets/icons/icon.svg" alt="" width="36" height="36">
      <div><b>{u['by']}</b> · {u['by_rest']}<br><span>{u['updated']} {upd} · {u['ambassador']} <a href="{P}/disclaimer">{u['foot_disclaimer']}</a></span></div>
    </div>
    <div class="prose">
{body}
    </div>
    {faq_html}
    <section class="block related"><p class="kicker">{u['keep_reading']}</p><div class="rel-grid">{rel_html}</div></section>
  </div>
</main>
""" + footer(lang)
    out = ROOT / page_path(lang, meta["slug"]).strip("/") / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(out_html)
    return out


def build_hub(lang, pages, alts):
    u, P = UI[lang], PREFIX[lang]
    meta = {"title": u["hub_title"], "description": u["hub_desc"], "h1_plain": u["hub_h1"],
            "published": "2026-10-09", "updated": max(p["updated"] for p in pages), "short": u["hub_short"], "og": "guides"}
    path = f"{P}/{HUB_SLUG[lang]}/"
    url = DOMAIN + path
    cards = "".join(f'<a class="guide-card" href="{page_path(lang, p["slug"])}"><span class="n">{i:02d}</span><b>{esc(p["short"])}</b><span>{esc(p["blurb"])}</span><em>{u["read"]}</em></a>'
                    for i, p in enumerate(pages, 1))
    hub_ld = [{"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "position": i, "url": DOMAIN + page_path(lang, p["slug"]), "name": p["short"]} for i, p in enumerate(pages, 1)]}]
    out_html = head(meta, url, hub_ld, lang, alts, kind="hub") + header(lang, alts) + f"""
<main class="article">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="{P}/">{u['crumb_home']}</a><span>›</span><span>{u['crumb_guides']}</span></nav>
    <p class="eyebrow">{u['hub_eyebrow']}</p>
    <h1>{u['hub_h1']}</h1>
    <p class="lede">{u['hub_lede']}</p>
    <div class="guide-grid">{cards}</div>
    {blocks(lang)['CLOSE']}
  </div>
</main>
""" + footer(lang)
    out = ROOT / path.strip("/") / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(out_html)


def load(lang):
    pages = []
    for f in sorted((SRC / lang).glob("*.html")):
        raw = f.read_text()
        m = re.match(r"\s*<!--META\s*(\{.*?\})\s*-->\s*(.*)", raw, re.S)
        if not m:
            raise SystemExit(f"{lang}/{f.name}: missing META block")
        meta = json.loads(m.group(1))
        meta["_body"] = m.group(2)
        pages.append(meta)
    pages.sort(key=lambda p: p["order"])
    return pages


def main():
    by_lang = {l: load(l) for l in LANG_ORDER}
    by_lang = {l: p for l, p in by_lang.items() if p}
    en = by_lang["en"]
    # og image name = English slug, so every language's preview shares a file name
    for l, pages in by_lang.items():
        for p in pages:
            p["og"] = next(e["slug"] for e in en if e["order"] == p["order"])

    sitemap = []
    for lang, pages in by_lang.items():
        for p in pages:
            alts = {l: page_path(l, q["slug"]) for l, ps in by_lang.items() for q in ps if q["order"] == p["order"]}
            out = build_page(lang, p, pages, alts)
            sitemap.append((page_path(lang, p["slug"]), "0.8", alts))
            print("built", out.relative_to(ROOT))
        hub_alts = {l: f"{PREFIX[l]}/{HUB_SLUG[l]}/" for l in by_lang}
        build_hub(lang, pages, hub_alts)
        sitemap.append((hub_alts[lang], "0.8", hub_alts))
        print("built", hub_alts[lang])

    # root pages (only languages that have been generated)
    for key, paths in ROOT_PAGES.items():
        if key in ("apply", "signup", "verify"):
            continue  # funnel steps are noindex
        have = {l: p for l, p in paths.items() if l == "en" or (ROOT / p.strip("/")).exists()
                or (ROOT / p.strip("/") / "index.html").exists()
                or (ROOT / (p.strip("/") + ".html")).exists()}
        pr = "1.0" if key == "home" else "0.3"
        for l, p in have.items():
            sitemap.append((p, pr, have))

    today = time.strftime("%Y-%m-%d")
    sm = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n')
    for path, pr, alts in sorted(sitemap, key=lambda x: (-float(x[1]), x[0])):
        sm += f"  <url>\n    <loc>{DOMAIN}{path}</loc>\n    <lastmod>{today}</lastmod>\n    <priority>{pr}</priority>\n"
        for l in LANG_ORDER:
            if l in alts:
                sm += f'    <xhtml:link rel="alternate" hreflang="{l}" href="{DOMAIN}{alts[l]}"/>\n'
        if "en" in alts:
            sm += f'    <xhtml:link rel="alternate" hreflang="x-default" href="{DOMAIN}{alts["en"]}"/>\n'
        sm += "  </url>\n"
    sm += "</urlset>\n"
    (ROOT / "sitemap.xml").write_text(sm)
    print("built sitemap.xml with", len(sitemap), "urls")


if __name__ == "__main__":
    main()
