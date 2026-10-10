# Renders the 1200x630 link-preview images for every guide page (and the
# translated homepages) into assets/img/og/<lang>/<english-slug>.jpg.
# Needs the unpacked @fontsource packages (anton, space-grotesk, jetbrains-mono,
# outfit) in FONTS, and Playwright with Chromium.
import asyncio
import sys
from playwright.async_api import async_playwright

FONTS = sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/fonts"
R = "/home/claude/ringomining"

BG = {
    "home": "fire-racks-wide.jpg", "gomining-promo-code": "fire-racks-wide.jpg", "is-gomining-legit": "fire-container.jpg",
    "gomining-review": "REVIEW", "is-gomining-profitable": "fire-cables.jpg", "gomining-calculator": "fire-shelves.jpg",
    "how-to-start-gomining": "fire-rack-tall.jpg", "gomining-reddit-reviews": "fire-wiring.jpg",
    "gomining-vs-buying-bitcoin": "fire-whatsminers.jpg", "guides": "fire-racks-wide.jpg",
}

# (headline with <em> highlights, [chip, chip])
TEXT = {
    "en": {
        "gomining-promo-code": ("GoMining promo code: <em>RINGO5</em>", ["First terahash on me", "10% back"]),
        "is-gomining-legit": ("Is GoMining <em>legit?</em> What I checked", ["Real farms on video", "10+ yrs in crypto"]),
        "gomining-review": ("My GoMining review: <em>$33,733</em> mined in 17 months", ["Real screenshots", "Gross vs net"]),
        "is-gomining-profitable": ("Is GoMining <em>profitable</em> in 2026?", ["Real net numbers", "Live staking APR"]),
        "gomining-calculator": ("What would <em>$1,000</em> earn on GoMining?", ["ROI calculator", "1 to 4 year projection"]),
        "how-to-start-gomining": ("How to start on GoMining, <em>step by step</em>", ["Beginner guide", "First terahash on me"]),
        "gomining-reddit-reviews": ("What people <em>really</em> say about GoMining", ["The good and the bad", "My honest take"]),
        "gomining-vs-buying-bitcoin": ("GoMining vs just buying Bitcoin: <em>why I mine</em>", ["4 ways to earn", "The 60/40 split"]),
        "guides": ("Everything I'd tell you about <em>GoMining</em>", ["8 straight-answer guides", "Real numbers"]),
    },
    "fr": {
        "home": ("J'ai gagné <em>1 M$</em> avec un meme coin. Puis j'ai vu mes machines <em>brûler.</em>", ["1er térahash pour moi", "10 % remboursés"]),
        "gomining-promo-code": ("Code promo GoMining : <em>RINGO5</em>", ["1er térahash pour moi", "10 % remboursés"]),
        "is-gomining-legit": ("GoMining est-il <em>fiable ?</em> Ce que j'ai vérifié", ["Vraies fermes en vidéo", "10+ ans de crypto"]),
        "gomining-review": ("Mon avis GoMining : <em>33 733 $</em> minés en 17 mois", ["Vraies captures", "Brut vs net"]),
        "is-gomining-profitable": ("GoMining est-il <em>rentable</em> en 2026 ?", ["Vrais chiffres nets", "APR de staking en direct"]),
        "gomining-calculator": ("Que rapporteraient <em>1 000 $</em> sur GoMining ?", ["Calculateur de rendement", "Projection 1 à 4 ans"]),
        "how-to-start-gomining": ("Commencer sur GoMining, <em>étape par étape</em>", ["Guide débutant", "1er térahash pour moi"]),
        "gomining-reddit-reviews": ("Ce que les gens disent <em>vraiment</em> de GoMining", ["Le bon et le moins bon", "Mon avis honnête"]),
        "gomining-vs-buying-bitcoin": ("GoMining ou acheter du Bitcoin : <em>pourquoi je mine</em>", ["4 façons de gagner", "La répartition 60/40"]),
        "guides": ("Tout ce que je vous dirais sur <em>GoMining</em>", ["8 guides francs", "Vrais chiffres"]),
    },
    "es": {
        "home": ("Gané <em>1 M$</em> con una meme coin. Luego vi mis máquinas <em>arder.</em>", ["1er terahash por mi cuenta", "10 % de vuelta"]),
        "gomining-promo-code": ("Código promocional GoMining: <em>RINGO5</em>", ["1er terahash por mi cuenta", "10 % de vuelta"]),
        "is-gomining-legit": ("¿GoMining es <em>confiable?</em> Lo que revisé", ["Granjas reales en video", "10+ años en cripto"]),
        "gomining-review": ("Mi opinión de GoMining: <em>33.733 $</em> minados en 17 meses", ["Capturas reales", "Bruto vs neto"]),
        "is-gomining-profitable": ("¿GoMining es <em>rentable</em> en 2026?", ["Números netos reales", "APR de staking en vivo"]),
        "gomining-calculator": ("¿Cuánto ganarían <em>1.000 $</em> en GoMining?", ["Calculadora de rentabilidad", "Proyección de 1 a 4 años"]),
        "how-to-start-gomining": ("Cómo empezar en GoMining, <em>paso a paso</em>", ["Guía para principiantes", "1er terahash por mi cuenta"]),
        "gomining-reddit-reviews": ("Lo que la gente dice <em>de verdad</em> de GoMining", ["Lo bueno y lo malo", "Mi opinión honesta"]),
        "gomining-vs-buying-bitcoin": ("¿GoMining o comprar Bitcoin? <em>Por qué mino</em>", ["4 formas de ganar", "El reparto 60/40"]),
        "guides": ("Todo lo que te contaría sobre <em>GoMining</em>", ["8 guías directas", "Números reales"]),
    },
    "de": {
        "home": ("<em>1 Mio. $</em> mit einem Memecoin. Dann sah ich meine Miner <em>brennen.</em>", ["Erster Terahash auf mich", "10 % zurück"]),
        "gomining-promo-code": ("GoMining Promo-Code: <em>RINGO5</em>", ["Erster Terahash auf mich", "10 % zurück"]),
        "is-gomining-legit": ("Ist GoMining <em>seriös?</em> Was ich geprüft habe", ["Echte Farmen im Video", "10+ Jahre Krypto"]),
        "gomining-review": ("Meine GoMining-Erfahrungen: <em>33.733 $</em> in 17 Monaten", ["Echte Screenshots", "Brutto vs. netto"]),
        "is-gomining-profitable": ("Lohnt sich <em>GoMining</em> 2026?", ["Echte Netto-Zahlen", "Live-Staking-APR"]),
        "gomining-calculator": ("Was bringen <em>1.000 $</em> auf GoMining?", ["Rendite-Rechner", "Prognose 1 bis 4 Jahre"]),
        "how-to-start-gomining": ("GoMining-Einstieg, <em>Schritt für Schritt</em>", ["Einsteiger-Anleitung", "Erster Terahash auf mich"]),
        "gomining-reddit-reviews": ("Was die Leute <em>wirklich</em> über GoMining sagen", ["Das Gute und das Schlechte", "Meine ehrliche Sicht"]),
        "gomining-vs-buying-bitcoin": ("GoMining oder Bitcoin kaufen: <em>Warum ich mine</em>", ["4 Wege zu verdienen", "Die 60/40-Aufteilung"]),
        "guides": ("Alles, was ich dir über <em>GoMining</em> sagen würde", ["8 klare Ratgeber", "Echte Zahlen"]),
    },
}

CSS = f"""@font-face{{font-family:Anton;src:url(file://{FONTS}/fontsource-anton-5.0.20/files/anton-latin-400-normal.woff2)}}
@font-face{{font-family:Anton;src:url(file://{FONTS}/fontsource-anton-5.0.20/files/anton-latin-ext-400-normal.woff2);unicode-range:U+0100-024F}}
@font-face{{font-family:SG;font-weight:700;src:url(file://{FONTS}/fontsource-space-grotesk-5.0.20/files/space-grotesk-latin-700-normal.woff2)}}
@font-face{{font-family:JB;src:url(file://{FONTS}/fontsource-jetbrains-mono-5.0.20/files/jetbrains-mono-latin-700-normal.woff2)}}
@font-face{{font-family:OF;font-weight:700;src:url(file://{FONTS}/fontsource-outfit/files/outfit-latin-700-normal.woff2)}}
*{{box-sizing:border-box}}body{{margin:0;width:1200px;height:630px;background:#0b0907;color:#f4ede4;font-family:SG;overflow:hidden;position:relative}}
.bg{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:.36;filter:saturate(1.2)}}
.shot{{position:absolute;right:-40px;top:70px;width:560px;border-radius:18px;border:1px solid #3b2c1b;opacity:.9;transform:rotate(-3deg);box-shadow:0 30px 80px rgba(0,0,0,.6)}}
.sh{{position:absolute;inset:0;background:linear-gradient(90deg,#0b0907 34%,rgba(11,9,7,.6) 70%,rgba(11,9,7,.25)),radial-gradient(700px 360px at 85% 120%,rgba(255,106,26,.45),transparent 70%)}}
.c{{position:absolute;inset:0;padding:56px 64px;display:flex;flex-direction:column}}
.brand{{display:flex;align-items:center;gap:14px}}.brand img{{width:48px;height:48px;border-radius:11px}}
.wm{{font-family:OF;font-weight:700;font-size:34px;letter-spacing:2.5px}}
.tag{{margin-left:auto;font-family:JB;font-size:18px;color:#f6b53a;letter-spacing:2px}}
h1{{font-family:Anton;font-weight:400;text-transform:uppercase;font-size:80px;line-height:1;margin:34px 0 0;max-width:820px}}
h1 em{{font-style:normal;color:#ff6a1a}}
.row{{margin-top:auto;display:flex;gap:12px;align-items:center}}
.code{{font-family:JB;font-size:30px;color:#f6b53a;border:2px solid #7a5520;background:#1f170b;border-radius:14px;padding:12px 20px;letter-spacing:2px;white-space:nowrap}}
.chip{{font-weight:700;font-size:22px;background:rgba(26,21,16,.92);border:1px solid #3b2c1b;border-radius:999px;padding:12px 18px;white-space:nowrap}}.chip span{{color:#ff6a1a}}"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--allow-file-access-from-files"])
        pg = await b.new_page(viewport={"width": 1200, "height": 630})
        for lang, items in TEXT.items():
            import os
            os.makedirs(f"{R}/assets/img/og/{lang}", exist_ok=True)
            for key, (h, chips) in items.items():
                bg = BG[key]
                back = (f'<img class="shot" src="file://{R}/assets/img/review/earnings.png">' if bg == "REVIEW"
                        else f'<div class="bg" style="background-image:url(file://{R}/assets/img/{bg})"></div>')
                ch = "".join(f'<div class="chip"><span>▲</span> {c}</div>' for c in chips)
                doc = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{back}<div class="sh"></div>'
                       f'<div class="c"><div class="brand"><img src="file://{R}/assets/icons/icon.svg"><span class="wm">RINGOMINING</span>'
                       f'<span class="tag">RINGOMINING.COM</span></div><h1>{h}</h1><div class="row"><div class="code">CODE: RINGO5</div>{ch}</div></div></body></html>')
                open("/tmp/claude-0/og_tmp.html", "w").write(doc)
                await pg.goto("file:///tmp/claude-0/og_tmp.html")
                await pg.wait_for_timeout(400)
                await pg.evaluate("""()=>{const h=document.querySelector('h1');let s=80;while(h.getBoundingClientRect().bottom>470&&s>44){s-=4;h.style.fontSize=s+'px'}}""")
                await pg.screenshot(path=f"{R}/assets/img/og/{lang}/{key}.jpg", type="jpeg", quality=86)
                print("og", lang, key)
        await b.close()


asyncio.run(main())
