#!/usr/bin/env python3
"""
Builds the RinGoMining guide pages from tools/pages/*.html.

Each page file starts with a JSON front-matter block between <!--META and -->,
followed by the article body HTML. The body can use these shared blocks:

  {{CODE_HINT}}  soft first CTA line           {{OFFER}}     bonus cards + button
  {{OPTIMIZER}}  GMT Optimizer embed           {{COMPARE}}   returns comparison + live APR
  {{APR}}        live staking APR (inline)     {{APR_DATE}}  date the APR was updated
  {{VIDEOS}}     the four VoskCoin farm tours  {{HABIT}}     $25-$50/week habit block
  {{STORY}}      short version of Ringo's story
  {{STEPS}}      the 3-step funnel with button {{CLOSE}}     final call to action

Run:  python3 tools/build_pages.py
Output: /<slug>/index.html for every page, plus /guides/index.html.
"""
import html
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "pages"
DOMAIN = "https://ringomining.com"
VER = time.strftime("%Y%m%d%H%M")
FONTS = ("https://fonts.googleapis.com/css2?family=Anton&family=Outfit:wght@600;700"
         "&family=JetBrains+Mono:wght@400;700&family=Space+Grotesk:wght@400;500;600;700&display=swap")

VIDEOS = [
    ("mPJrgF6LXLk", "GoMining's HUGE Texas Mining Farm", "Texas"),
    ("Pd-QxvRNg9I", "West Texas Bitcoin Mining Farm Tour", "West Texas"),
    ("ukaVbFeufcU", "Mining Bitcoins in South Carolina", "South Carolina"),
    ("YOyW-LmCBnY", "Huge Mining Farm Tour", "South Carolina"),
]


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- blocks
APR = '<span data-gomining-rate>21.7%</span>'
APR_DATE = '<span data-gomining-rate="updated">Oct 7, 2026</span>'

BLOCKS = {
    "APR": APR,
    "APR_DATE": APR_DATE,
    "CODE_HINT": """
<aside class="callout callout-code">
  <div>
    <strong>Use code RINGO5 and the first terahash is on me.</strong>
    <span>Plus 10% back, up to $1,000, and 5% back on every upgrade for life.</span>
  </div>
  <a class="btn btn-fire" href="/apply.html">Claim my bonus →</a>
</aside>""",
    "OFFER": """
<section class="block offer-block" id="bonus">
  <p class="kicker">The RINGO5 bonus</p>
  <h2>Everyone's got a code. Here's why you'd use mine.</h2>
  <p>It's my way of saying thanks. I really appreciate it when people use my code, so I reward them for it, paid by me, in crypto.</p>
  <div class="offer">
    <div class="card"><div class="big">$18.99</div><h3>The first terahash is on me</h3><p>You buy your first terahash, I send the $18.99 back.</p></div>
    <div class="card"><div class="big">10%</div><h3>Cash back</h3><p>10% back on your purchases, up to $10,000. That's up to $1,000 in your wallet.</p></div>
    <div class="card"><div class="big">5%</div><h3>Back for life</h3><p>5% back on every terahash upgrade you ever make.</p></div>
    <div class="card"><div class="big">24h</div><h3>Fast payout</h3><p>USDC, Bitcoin, or GoMining tokens within 24 hours of confirming your purchase.</p></div>
  </div>
  <p class="fine">Putting in more than $10,000? Message me and I'll take care of you. <a href="/disclaimer.html">Bonus terms</a>.</p>
  <a class="btn btn-fire" href="/apply.html">Lock in my RINGO5 bonus →</a>
</section>""",
    "OPTIMIZER": """
<section class="block optimizer-block" id="calculator">
  <p class="kicker">GMT Optimizer</p>
  <h2>Run your own numbers</h2>
  <p>Don't take my word for it. Type in what you'd put in, set the Bitcoin price you want to test, and the Optimizer splits it between terahash and GoMining tokens for the maximum discount, then projects your returns at today's rates.</p>
  <div class="optimizer-box"><div data-gmt-optimizer data-src="funnel"></div></div>
  <p class="fine">Projections, not promises. Bitcoin's price, network difficulty, and rewards change every day. Not financial advice.</p>
</section>""",
    "COMPARE": f"""
<section class="block compare-block">
  <p class="kicker">Where else would your money go?</p>
  <h2>You know what your other money does. Here's what this does.</h2>
  <div class="rows">
    <div class="row"><span class="what">Savings account</span><span class="val muted">Barely moves</span></div>
    <div class="row"><span class="what">USDC staking</span><span class="val">~3–5% a year<sup><a href="#src-usdc">1</a></sup></span></div>
    <div class="row"><span class="what">S&amp;P 500</span><span class="val">~10% a year, long-run average<sup><a href="#src-sp">2</a></sup></span></div>
    <div class="row"><span class="what">Mining on your own machines</span><span class="val bad">At average US power prices, often costs more than it earns</span></div>
    <div class="row hi"><span class="what">GoMining token staking (veGOMINING)</span><span class="val">{APR} APR right now, <em>plus</em> your daily Bitcoin mining rewards</span></div>
  </div>
  <p class="fine">Staking APR is live from my GMT Optimizer feed, updated {APR_DATE}. It moves; it's ranged roughly 22–25% over the past few months. Rates change. Not financial advice.</p>
  <ol class="sources">
    <li id="src-usdc">USDC yields across major platforms ran about 3–5% APY in October 2026 (<a href="https://eco.com/support/en/articles/15182156-usdc-yield-2026" target="_blank" rel="noopener">Eco</a>).</li>
    <li id="src-sp">The S&amp;P 500 has averaged about 10% a year since 1957 (<a href="https://www.fidelity.com/learning-center/trading-investing/sp-500-average-return" target="_blank" rel="noopener">Fidelity</a>).</li>
  </ol>
</section>""",
    "HABIT": """
<section class="block habit-block">
  <p class="kicker">You don't need to be rich</p>
  <h2>$25 to $50 a week. That's not even hustling.</h2>
  <div class="habit">
    <div><b>$25</b><span>a week</span><em>= $1,300 a year</em></div>
    <div><b>$50</b><span>a week</span><em>= $2,600 a year</em></div>
    <div class="hl"><b>$13,000</b><span>in 5 years at $50/week</span><em>before it earns a thing</em></div>
  </div>
  <p>And that's just the money you set aside. On GoMining it's mining Bitcoin and earning staking rewards the whole time, and those rewards can go right back in. If you're serious about building something, you can find $25 a week. Once you see the numbers, you'll probably want to do more.</p>
  <a class="btn btn-ghost" href="#calculator">See what that habit could grow into →</a>
</section>""",
    "STORY": """
<aside class="story-box">
  <p class="kicker">Who's writing this</p>
  <p><strong>I'm Ringo.</strong> I've been in crypto for 10+ years. I held Bitcoin through the crash under $10k, bought XRP under 30 cents, and swapped over $1M of Shiba Inu in a single transaction. I took that money and built a real mining farm in Rochester, Michigan. When Bitcoin fell, the power bill ate the profits, so I shipped my machines to Toluca, Mexico for cheaper power. That facility burned down, and a technician there lost his life.</p>
  <p>That's why I mine on GoMining now: Bitcoin mining without owning machines that can break, burn, or hurt someone. <a href="/#story">Read the full story →</a></p>
</aside>""",
    "STEPS": """
<section class="block steps-block">
  <p class="kicker">Get your bonus in 3 steps</p>
  <h2>Three minutes. Three steps.</h2>
  <ol class="mini-steps">
    <li><b>Tell me who you are</b><span>A one-minute form so I can find you and pay you.</span></li>
    <li><b>Sign up with RINGO5</b><span>GoMining opens in a new tab with my code attached. Buy your first terahash.</span></li>
    <li><b>Send me your GoMining ID</b><span>I confirm it and pay your bonus within 24 hours.</span></li>
  </ol>
  <a class="btn btn-fire" href="/apply.html">Start step 1 →</a>
</section>""",
    "CLOSE": """
<section class="close-band">
  <h2>Stop waiting. Start mining.</h2>
  <p>Nobody's coming to change your situation for you. The people who win are the ones who stop thinking about it and make a move.</p>
  <a class="btn btn-fire" href="/apply.html">Start with RINGO5 →</a>
</section>""",
}


def videos_block():
    items = []
    for vid, title, place in VIDEOS:
        items.append(f"""
    <figure class="yt">
      <button class="yt-lite" type="button" data-yt="{vid}" aria-label="Play: {esc(title)}">
        <img src="https://i.ytimg.com/vi/{vid}/hqdefault.jpg" alt="" loading="lazy" width="480" height="360">
        <span class="yt-play" aria-hidden="true"></span>
      </button>
      <figcaption><b>{esc(place)}</b> · VoskCoin: “{esc(title)}”</figcaption>
    </figure>""")
    return f"""
<section class="block videos-block">
  <div class="yt-grid">{''.join(items)}
  </div>
  <p class="fine">Videos by VoskCoin, an independent crypto YouTuber. Facility figures mentioned in them come from GoMining.</p>
</section>"""


BLOCKS["VIDEOS"] = videos_block()


# ---------------------------------------------------------------- page shell
def head(meta, url, extra_ld):
    t, d = esc(meta["title"]), esc(meta["description"])
    own = f"/assets/img/og/{meta.get('slug', '')}.jpg"
    img = DOMAIN + meta.get("image", own if (ROOT / own.lstrip("/")).exists() else "/assets/img/og-image.jpg")
    ld = [{
        "@context": "https://schema.org", "@type": "Article",
        "headline": meta["h1_plain"], "description": meta["description"],
        "image": img, "datePublished": meta["published"], "dateModified": meta["updated"],
        "author": {"@type": "Person", "name": "Ringo", "url": DOMAIN + "/"},
        "publisher": {"@type": "Organization", "name": "RinGoMining", "url": DOMAIN + "/",
                      "logo": {"@type": "ImageObject", "url": DOMAIN + "/assets/icons/icon-512.png"}},
        "mainEntityOfPage": url,
    }, {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": DOMAIN + "/"},
            {"@type": "ListItem", "position": 2, "name": "Guides", "item": DOMAIN + "/guides/"},
            {"@type": "ListItem", "position": 3, "name": meta["short"], "item": url},
        ],
    }] + extra_ld
    ld_html = "\n".join('  <script type="application/ld+json">' + json.dumps(x, ensure_ascii=False) + "</script>" for x in ld)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{t}</title>
  <meta name="description" content="{d}">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <link rel="canonical" href="{url}">
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="RinGoMining">
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


HEADER = """<header class="bar">
  <div class="wrap">
    <a class="brand" href="/"><img class="brand-mark" src="/assets/icons/icon.svg" alt="" width="30" height="30"><span>RINGOMINING</span></a>
    <nav class="bar-nav"><a href="/guides/">Guides</a><button class="code-pill" data-copy="RINGO5" title="Copy code">CODE: RINGO5 ⧉</button></nav>
  </div>
</header>
"""


def footer():
    return f"""
<footer>
  <div class="wrap">
    <nav class="foot-links"><a href="/">Home</a><a href="/guides/">All guides</a><a href="/apply.html">Claim the RINGO5 bonus</a><a href="/disclaimer.html">Disclaimer</a></nav>
    <div class="contact-row">
      <span>Telegram: <a data-tg>@RingoShitoitsu</a></span>
      <span>Phone: <a data-phone>906-235-5711</a></span>
      <span>Referral code: <strong style="color:var(--gold)">RINGO5</strong></span>
    </div>
    <p style="margin:0">RinGoMining is run by Ringo, an independent GoMining ambassador. Not affiliated with, employed by, or speaking for GoMining. Nothing here is financial advice. Mining rewards are not guaranteed and you can lose money. <a href="/disclaimer.html">Read the full disclaimer</a>.</p>
  </div>
</footer>

<div class="sticky-cta"><a class="btn btn-fire btn-block" href="/apply.html">Claim my RINGO5 bonus →</a></div>

<script src="/assets/js/config.js?v={VER}"></script>
<script src="/assets/js/main.js?v={VER}"></script>
<script src="/assets/js/article.js?v={VER}"></script>
<script src="https://gmt-optimizer.com/assets/rates.js" async></script>
<script src="https://gmt-optimizer.com/assets/embed.js" async></script>
</body>
</html>
"""


def fmt_date(iso):
    return time.strftime("%B %-d, %Y", time.strptime(iso, "%Y-%m-%d"))


def build_page(meta, body, pages):
    url = f"{DOMAIN}/{meta['slug']}/"
    for k, v in BLOCKS.items():
        body = body.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", body)
    if left:
        raise SystemExit(f"{meta['slug']}: unknown blocks {left}")

    faq_html, extra_ld = "", []
    if meta.get("faq"):
        items = "".join(f"<details><summary>{esc(q)}</summary><p>{a}</p></details>" for q, a in meta["faq"])
        faq_html = f'<section class="block faq"><p class="kicker">Quick answers</p><h2>FAQ</h2>{items}</section>'
        extra_ld.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": re.sub(r"<[^>]+>", "", a)}}
            for q, a in meta["faq"]]})

    related = [p for p in pages if p["slug"] != meta["slug"]]
    rel_html = "".join(f'<a class="rel" href="/{p["slug"]}/"><b>{esc(p["short"])}</b><span>{esc(p["blurb"])}</span></a>' for p in related)

    upd = fmt_date(meta["updated"])
    page = head(meta, url, extra_ld) + HEADER + f"""
<main class="article">
  <div class="wrap narrow">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a><span>›</span><a href="/guides/">Guides</a><span>›</span><span>{esc(meta['short'])}</span></nav>
    <p class="eyebrow">{esc(meta['eyebrow'])}</p>
    <h1>{meta['h1']}</h1>
    <p class="lede">{meta['lede']}</p>
    <div class="byline">
      <img src="/assets/icons/icon.svg" alt="" width="36" height="36">
      <div><b>By Ringo</b> · 10+ years in crypto · mining on GoMining since May 2025<br><span>Updated {upd} · Independent GoMining ambassador, not affiliated with GoMining. Links use my referral code. <a href="/disclaimer.html">Disclaimer</a></span></div>
    </div>
    <div class="prose">
{body}
    </div>
    {faq_html}
    <section class="block related"><p class="kicker">Keep reading</p><div class="rel-grid">{rel_html}</div></section>
  </div>
</main>
""" + footer()
    out = ROOT / meta["slug"] / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page)
    return out


def build_hub(pages):
    meta = {"title": "GoMining Guides: Promo Code, Reviews, Profitability & More | RinGoMining",
            "description": "Straight answers about GoMining from a 10-year crypto veteran who mines on it every day: the RINGO5 promo code, is it legit, real results, profitability, and how to start.",
            "h1_plain": "GoMining guides", "published": "2026-10-09", "updated": pages[0]["updated"],
            "short": "Guides", "slug": "guides"}
    url = DOMAIN + "/guides/"
    cards = "".join(f'<a class="guide-card" href="/{p["slug"]}/"><span class="n">{i:02d}</span><b>{esc(p["short"])}</b><span>{esc(p["blurb"])}</span><em>Read →</em></a>'
                    for i, p in enumerate(pages, 1))
    hub_ld = [{"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "position": i, "url": f"{DOMAIN}/{p['slug']}/", "name": p["short"]} for i, p in enumerate(pages, 1)]}]
    page = head(meta, url, hub_ld).replace('"@type": "Article"', '"@type": "CollectionPage"').replace('property="og:type" content="article"', 'property="og:type" content="website"') + HEADER + f"""
<main class="article">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a><span>›</span><span>Guides</span></nav>
    <p class="eyebrow">Straight answers · Code RINGO5</p>
    <h1>Everything I'd tell you about GoMining</h1>
    <p class="lede">I've been in crypto for 10+ years and mining on GoMining since May 2025. These are the questions people ask me most, answered with my real numbers.</p>
    <div class="guide-grid">{cards}</div>
    {BLOCKS['CLOSE']}
  </div>
</main>
""" + footer()
    (ROOT / "guides").mkdir(exist_ok=True)
    (ROOT / "guides" / "index.html").write_text(page)


def main():
    pages = []
    for f in sorted(SRC.glob("*.html")):
        raw = f.read_text()
        m = re.match(r"\s*<!--META\s*(\{.*?\})\s*-->\s*(.*)", raw, re.S)
        if not m:
            raise SystemExit(f"{f.name}: missing META block")
        meta = json.loads(m.group(1))
        meta["_body"] = m.group(2)
        pages.append(meta)
    pages.sort(key=lambda p: p["order"])
    for p in pages:
        out = build_page(p, p["_body"], pages)
        print("built", out.relative_to(ROOT))
    build_hub(pages)
    print("built guides/index.html")

    # sitemap
    urls = [("/", "1.0"), ("/guides/", "0.8")] + [(f"/{p['slug']}/", "0.8") for p in pages] + [("/disclaimer.html", "0.3")]
    today = time.strftime("%Y-%m-%d")
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sm += "".join(f"  <url><loc>{DOMAIN}{u}</loc><lastmod>{today}</lastmod><priority>{pr}</priority></url>\n" for u, pr in urls)
    sm += "</urlset>\n"
    (ROOT / "sitemap.xml").write_text(sm)
    print("built sitemap.xml")


if __name__ == "__main__":
    main()
