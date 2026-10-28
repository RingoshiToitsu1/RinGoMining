"""Render faceless 1080x1920 Shorts from Ringo's real photos + on-screen captions."""
import os, subprocess, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
IMG = f"{REPO}/assets/img"
OUT = os.path.abspath(sys.argv[1])
W, H, FPS = 1080, 1920, 30
BG, TEXT, EMBER, GOLD, MUTED = (11, 9, 7), (244, 237, 228), (255, 106, 26), (246, 181, 58), (169, 157, 144)
BLACK = "/usr/share/fonts/truetype/inter/Inter-Black.ttf"
BOLD = "/usr/share/fonts/truetype/inter/Inter-Bold.ttf"


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(subprocess.check_output(["fc-match", "-f", "%{file}", "Inter:black" if path == BLACK else "Inter:bold"]).decode(), size)


def cover(im, w, h):
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    l, t = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((l, t, l + w, t + h))


def draw_rich(d, text, y, size, color=TEXT, maxw=960):
    """Centered, word-wrapped text; *word* renders in ember. Returns bottom y."""
    f = font(BLACK, size)
    words = [(w.strip("*"), w.startswith("*")) for w in text.split()]
    lines, cur = [], []
    for w in words:
        trial = " ".join(x for x, _ in cur + [w])
        if cur and d.textlength(trial, font=f) > maxw:
            lines.append(cur); cur = [w]
        else:
            cur.append(w)
    lines.append(cur)
    for line in lines:
        total = d.textlength(" ".join(x for x, _ in line), font=f)
        x = (W - total) / 2
        for word, hi in line:
            d.text((x, y), word, font=f, fill=EMBER if hi else color, stroke_width=6, stroke_fill=(0, 0, 0))
            x += d.textlength(word + " ", font=f)
        y += int(size * 1.18)
    return y


def scene(photo, text, sub=None, size=92, text_y=None):
    if photo:
        src = Image.open(f"{IMG}/{photo}").convert("RGB")
        canvas = cover(src, W, H).filter(ImageFilter.GaussianBlur(40))
        canvas = Image.blend(canvas, Image.new("RGB", (W, H), BG), 0.55)
        fg = src.copy()
        s = min(W / fg.width, 1300 / fg.height)
        fg = fg.resize((int(fg.width * s), int(fg.height * s)), Image.LANCZOS)
        canvas.paste(fg, ((W - fg.width) // 2, 560 + (1300 - fg.height) // 2))
        ty = 170 if text_y is None else text_y
    else:
        canvas = Image.new("RGB", (W, H), BG)
        glow = Image.new("RGB", (W, H), BG)
        ImageDraw.Draw(glow).ellipse((-300, 1200, 1380, 2600), fill=(90, 34, 8))
        canvas = glow.filter(ImageFilter.GaussianBlur(180))
        ty = 700 if text_y is None else text_y
    d = ImageDraw.Draw(canvas)
    y = draw_rich(d, text, ty, size)
    if sub:
        draw_rich(d, sub, y + 30, int(size * 0.55), color=GOLD)
    return canvas


def end_card():
    c = scene(None, "Code *RINGO5*", "5% bonus terahash from GoMining", size=130, text_y=640)
    d = ImageDraw.Draw(c)
    logo = Image.open(f"{REPO}/assets/brand/ringomining-logo-white-text.png").convert("RGBA")
    s = 620 / logo.width
    logo = logo.resize((620, int(logo.height * s)), Image.LANCZOS)
    c.paste(logo, ((W - 620) // 2, 300), logo)
    draw_rich(d, "ringomining.com/apply", 1180, 66, color=TEXT)
    f = font(BOLD, 34)
    for i, note in enumerate(["Referral code. I get a bonus when you use it.", "Not financial advice."]):
        d.text(((W - d.textlength(note, font=f)) / 2, 1680 + i * 48), note, font=f, fill=MUTED)
    return c


def render(name, scenes):
    tmp = f"{OUT}/_{name}"
    os.makedirs(tmp, exist_ok=True)
    parts = []
    for i, (img, secs) in enumerate(scenes):
        png, mp4 = f"{tmp}/{i}.png", f"{tmp}/{i}.mp4"
        img.save(png)
        n = int(secs * FPS)
        vf = (f"scale={W*2}:{H*2},zoompan=z='1+0.06*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={n}:s={W}x{H}:fps={FPS},fade=t=in:st=0:d=0.15,format=yuv420p")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", png, "-vf", vf,
                        "-frames:v", str(n), "-c:v", "libx264", "-preset", "medium", "-crf", "20", mp4], check=True)
        parts.append(mp4)
    with open(f"{tmp}/list.txt", "w") as fh:
        fh.writelines(f"file '{p}'\n" for p in parts)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{tmp}/list.txt",
                    "-c", "copy", "-movflags", "+faststart", f"{OUT}/{name}.mp4"], check=True)
    scenes[0][0].save(f"{OUT}/{name}-cover.png")


VIDEOS = {
    "01-my-miners-burned-down": [
        (scene("fire-racks-wide.jpg", "I spent over *$600,000* on bitcoin miners"), 3.0),
        (scene("fire-rack-tall.jpg", "Moved them to a hosting facility in *Mexico*"), 2.8),
        (scene("fire-whatsminers.jpg", "And they *burned down.*"), 2.6),
        (scene("fire-shelves.jpg", "Power. Repairs. Hosting. *Fire.* All on me."), 3.0),
        (scene(None, "Now *GoMining* runs the machines", "I just collect the bitcoin"), 2.8),
        (scene("review/earnings.png", "Over *$33,000* in mining rewards so far", text_y=300), 3.4),
        (end_card(), 3.4),
    ],
    "02-how-i-cash-out": [
        (scene("review/earnings.png", "How I cash out my *bitcoin mining*", text_y=300), 2.8),
        (scene(None, "Step 1", "Withdraw on BNB Smart Chain"), 2.4),
        (scene(None, "Step 2", "Swap to USDC"), 2.0),
        (scene(None, "Step 3", "Send to Coinbase, then my bank"), 2.4),
        (scene(None, "Or just *tap to pay*", "GoMining Visa card in Apple Pay"), 2.8),
        (scene(None, "Real talk", "Fees take about half. Only invest what you can afford to lose."), 3.4),
        (end_card(), 3.4),
    ],
    "03-dont-make-this-mistake": [
        (scene(None, "Don't buy GoMining terahash *yet*", size=110), 2.6),
        (scene(None, "Put a referral code on your account *first*"), 2.8),
        (scene(None, "Add it after your first purchase?", "You can miss the bonus."), 3.0),
        (scene(None, "Sign up with *RINGO5*", "Then buy your terahash"), 2.6),
        (end_card(), 3.4),
    ],
    "04-i-ran-a-mining-facility": [
        (scene("fire-container.jpg", "I ran my own *bitcoin mining* facility"), 2.8),
        (scene("fire-wiring.jpg", "Here's what nobody tells you"), 2.4),
        (scene("fire-cables.jpg", "Power bills. Repairs. Hosting. *Shipping.*"), 2.8),
        (scene("fire-racks-wide.jpg", "And if it burns down? *That's on you.*"), 3.0),
        (scene(None, "I'd never go back", "Now GoMining runs the machines for me"), 2.8),
        (end_card(), 3.4),
    ],
}

def from_spec(path):
    """JSON spec: {"video-name": [{"photo": "fire-cables.jpg" or null, "text": "...", "sub": "...", "secs": 2.8}, ..., {"end": true}]}.
    Photos are relative to assets/img. Wrap a word in *asterisks* to color it orange."""
    import json
    spec = json.load(open(path))
    return {name: [(end_card(), 3.4) if s.get("end") else
                   (scene(s.get("photo"), s["text"], s.get("sub"), s.get("size", 92), s.get("text_y")), s.get("secs", 2.8))
                   for s in scenes] for name, scenes in spec.items()}


if __name__ == "__main__":
    # Usage: make_shorts.py OUT_DIR [spec.json]   (no spec renders the built-in starter set)
    os.makedirs(OUT, exist_ok=True)
    videos = from_spec(sys.argv[2]) if len(sys.argv) > 2 else VIDEOS
    for name, sc in videos.items():
        render(name, sc)
        print("done", name)
