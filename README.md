# RinGoMining

Referral funnel for Ringo's GoMining code **RINGO5**.

| Page | Purpose |
|---|---|
| `index.html` | Landing page: story, SHIB proof, fire photos, GoMining vs cloud mining, GMT Optimizer, bonus offer, FAQ |
| `apply.html` | Step 1: contact info (Google Form #1) |
| `signup.html` | Step 2: create GoMining account with RINGO5 (opens in a new tab) |
| `verify.html` | Step 3: GoMining ID + wallet details (Google Form #2) |
| `disclaimer.html` | Not-financial-advice, risks, bonus terms |

## Go live (GitHub Pages)

Settings → Pages → Source: **Deploy from a branch** → Branch: **main** / **(root)** → Save.
The site appears at `https://ringoshitoitsu1.github.io/RinGoMining/` within a few minutes.

## Connect the Google Forms

Make two forms at forms.google.com, then paste each embed URL into `assets/js/config.js`.

**Form 1: "RinGoMining · Step 1: Your info"**
1. Full name (short answer, required)
2. Phone number (short answer)
3. Email (short answer, required, "Response validation: email")
4. Telegram handle (short answer)
5. How much are you planning to start with? (multiple choice: Just the first terahash / $100–$499 / $500–$999 / $1,000–$4,999 / $5,000–$10,000 / $10,000+)
6. How do you want your bonus paid? (multiple choice: USDC / Bitcoin / GoMining token)
7. Best way to reach you? (multiple choice: Telegram / Text / Call / Email)
8. Questions or concerns (paragraph)

**Form 2: "RinGoMining · Step 3: Confirm & get paid"**
1. Email you used in Step 1 (short answer, required)
2. GoMining ID (short answer, required, "Response validation: number")
3. Payout coin (multiple choice: USDC / Bitcoin / GoMining token, required)
4. Chain / network (short answer, required, e.g. Ethereum, BNB Chain, Bitcoin, Solana)
5. Wallet address (short answer, required)
6. Anything else I should know? (paragraph)

For each form: **Send → `< >` (embed) → copy the `src="…"` URL** and paste it into `FORM_STEP1_URL` / `FORM_STEP3_URL`. Turn on Settings → Responses → **Collect email addresses** off (we ask for it), and link both forms to one Google Sheet so you can match people by email.

Until a URL is filled in, each step shows a Telegram/phone fallback so nobody gets stuck.

## Admin lead tracker (`admin.html`)

Private page to track every lead (New → Reached out → Waiting on purchase → Confirmed → Paid).
Data lives in the RinGoMining Leads sheet; tracking is saved to its **Tracker** tab.
The backend is `tools/admin-backend.gs` (an Apps Script web app). The login is checked there,
so no password is stored in this repo. Paste the web app URL into `ADMIN_API` in `assets/js/config.js`.

## Guide pages (SEO)

Source for the 8 guide pages lives in `tools/pages/*.html` (JSON front matter + body).
Shared blocks (offer, calculator, live staking APR, videos, CTAs) are defined in `tools/build_pages.py`.
After editing a page, run `python3 tools/build_pages.py` to regenerate `/<slug>/index.html`, `/guides/` and `sitemap.xml`.
The staking APR updates itself from https://gmt-optimizer.com/api/rates.json via `rates.js`.
