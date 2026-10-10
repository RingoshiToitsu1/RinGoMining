# Renders the 1200x630 link-preview image for each guide page into assets/img/og/.
import asyncio, json
from playwright.async_api import async_playwright
F='/tmp/claude-0/fonts'  # unpacked @fontsource packages: anton, space-grotesk, jetbrains-mono, outfit
R='/home/claude/ringomining'
pages=[
 ('gomining-promo-code','GoMining promo code: <em>RINGO5</em>','fire-racks-wide.jpg',['First terahash on me','10% back']),
 ('is-gomining-legit','Is GoMining <em>legit?</em> What I checked','fire-container.jpg',['Real farms on video','10+ yrs in crypto']),
 ('gomining-review','My GoMining review: <em>$33,733</em> mined in 17 months','REVIEW',['Real screenshots','Gross vs net']),
 ('is-gomining-profitable','Is GoMining <em>profitable</em> in 2026?','fire-cables.jpg',['Real net numbers','Live staking APR']),
 ('gomining-calculator','What would <em>$1,000</em> earn on GoMining?','fire-shelves.jpg',['ROI calculator','1 to 4 year projection']),
 ('how-to-start-gomining','How to start on GoMining, <em>step by step</em>','fire-rack-tall.jpg',['Beginner guide','First terahash on me']),
 ('gomining-reddit-reviews','What people <em>really</em> say about GoMining','fire-wiring.jpg',['The good and the bad','My honest take']),
 ('gomining-vs-buying-bitcoin','GoMining vs just buying Bitcoin: <em>why I mine</em>','fire-whatsminers.jpg',['4 ways to earn','The 60/40 split']),
 ('guides','Everything I\'d tell you about <em>GoMining</em>','fire-racks-wide.jpg',['8 straight-answer guides','Real numbers']),
]
CSS=f'''@font-face{{font-family:Anton;src:url(file://{F}/fontsource-anton-5.0.20/files/anton-latin-400-normal.woff2)}}
@font-face{{font-family:SG;font-weight:700;src:url(file://{F}/fontsource-space-grotesk-5.0.20/files/space-grotesk-latin-700-normal.woff2)}}
@font-face{{font-family:JB;src:url(file://{F}/fontsource-jetbrains-mono-5.0.20/files/jetbrains-mono-latin-700-normal.woff2)}}
@font-face{{font-family:OF;font-weight:700;src:url(file://{F}/fontsource-outfit/files/outfit-latin-700-normal.woff2)}}
*{{box-sizing:border-box}}body{{margin:0;width:1200px;height:630px;background:#0b0907;color:#f4ede4;font-family:SG;overflow:hidden;position:relative}}
.bg{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:.36;filter:saturate(1.2)}}
.shot{{position:absolute;right:-40px;top:70px;width:560px;border-radius:18px;border:1px solid #3b2c1b;opacity:.9;transform:rotate(-3deg);box-shadow:0 30px 80px rgba(0,0,0,.6)}}
.sh{{position:absolute;inset:0;background:linear-gradient(90deg,#0b0907 34%,rgba(11,9,7,.6) 70%,rgba(11,9,7,.25)),radial-gradient(700px 360px at 85% 120%,rgba(255,106,26,.45),transparent 70%)}}
.c{{position:absolute;inset:0;padding:56px 64px;display:flex;flex-direction:column}}
.brand{{display:flex;align-items:center;gap:14px}}.brand img{{width:48px;height:48px;border-radius:11px}}
.wm{{font-family:OF;font-weight:700;font-size:34px;letter-spacing:2.5px}}
.tag{{margin-left:auto;font-family:JB;font-size:18px;color:#f6b53a;letter-spacing:2px}}
h1{{font-family:Anton;font-weight:400;text-transform:uppercase;font-size:80px;line-height:1;margin:34px 0 0;max-width:800px}}
h1 em{{font-style:normal;color:#ff6a1a}}
.row{{margin-top:auto;display:flex;gap:12px;align-items:center}}
.code{{font-family:JB;font-size:30px;color:#f6b53a;border:2px solid #7a5520;background:#1f170b;border-radius:14px;padding:12px 20px;letter-spacing:2px}}
.chip{{font-weight:700;font-size:22px;background:rgba(26,21,16,.92);border:1px solid #3b2c1b;border-radius:999px;padding:12px 18px}}.chip span{{color:#ff6a1a}}'''
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=['--allow-file-access-from-files'])
        pg=await b.new_page(viewport={'width':1200,'height':630})
        for slug,h,bg,chips in pages:
            if bg=='REVIEW':
                back=f'<img class="shot" src="file://{R}/assets/img/review/earnings.png">'
            else:
                back=f'<div class="bg" style="background-image:url(file://{R}/assets/img/{bg})"></div>'
            ch=''.join(f'<div class="chip"><span>▲</span> {c}</div>' for c in chips)
            html=f'<!doctype html><html><head><style>{CSS}</style></head><body>{back}<div class="sh"></div><div class="c"><div class="brand"><img src="file://{R}/assets/icons/icon.svg"><span class="wm">RINGOMINING</span><span class="tag">RINGOMINING.COM</span></div><h1>{h}</h1><div class="row"><div class="code">CODE: RINGO5</div>{ch}</div></div></body></html>'
            open('/tmp/claude-0/og_tmp.html','w').write(html)
            await pg.goto('file:///tmp/claude-0/og_tmp.html'); await pg.wait_for_timeout(500)
            # shrink headline if it overflows
            await pg.evaluate("""()=>{const h=document.querySelector('h1');let s=80;while(h.getBoundingClientRect().bottom>470&&s>50){s-=4;h.style.fontSize=s+'px'}}""")
            await pg.screenshot(path=f'{R}/assets/img/og/{slug}.jpg',type='jpeg',quality=86)
            print('og',slug)
        await b.close()
asyncio.run(main())
