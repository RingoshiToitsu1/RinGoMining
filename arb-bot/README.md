# arbbot: Kalshi × Polymarket arbitrage trading system

An autonomous, Telegram-controlled bot that looks for the same event priced differently on Kalshi and
Polymarket, buys YES on one and NO on the other when the pair costs less than the $1 it pays out, and
manages everything that can go wrong in between.

It is not a directional betting bot. Every position is two legs that offset the same outcome.

**It starts in paper mode with trading off and zero capital allocated.** Nothing reaches a real
exchange until you have run a paper period, both exchanges pass their eligibility checks, and you type an
explicit confirmation in Telegram.

## What is in the box

| Stage | Where |
|---|---|
| Infrastructure, config, event bus, persistence | `arbbot/app.py`, `config/`, `core/events.py`, `database/`, `infra/cache.py` |
| Exchange adapters (independent, swappable) | `exchanges/kalshi/`, `exchanges/polymarket/us.py`, `exchanges/polymarket/intl.py` |
| Live order-book ingestion (REST polling + WebSocket) | `data/orderbooks.py` |
| Market discovery | `data/market_discovery.py` |
| Market matching (semantic + deterministic + rule verification) | `core/market_matcher.py`, `core/llm_verifier.py` |
| Dynamic profitability engine | `core/profitability.py`, `core/fees.py` |
| Capital allocation and fractional-Kelly sizing | `core/capital_allocator.py` |
| Independent risk manager | `core/risk_manager.py` |
| Paper trading (real data, simulated fills) | `exchanges/sim.py` |
| Execution state machine, journal, restart recovery | `core/execution_engine.py` |
| Scan loop, settlement tracking | `core/arbitrage_engine.py` |
| Telegram command center and alerts | `bot/telegram/` |
| Backtesting | `backtesting/` |
| Docker, migrations | `Dockerfile`, `deployment/docker-compose.yml`, `database/migrations/` |

## Quick start

1. Create a Telegram bot with @BotFather and get your numeric user id from @userinfobot.
2. `cp .env.example .env` and fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ADMIN_IDS`. That is enough for
   paper trading: public market data needs no exchange keys.
3. Start it:

   ```bash
   docker compose -f deployment/docker-compose.yml up -d --build
   docker compose -f deployment/docker-compose.yml logs -f bot
   ```

4. In Telegram:

   ```
   /start
   /capital kalshi 500          your numbers, per exchange
   /capital polymarket 500
   /mappings                    review the market pairs discovery found
   /mappings show 12            read both rule sets
   /mappings approve 12         only if they settle identically
   /autotrade on                paper trading begins
   /dashboard
   ```

Without Docker: Python 3.12+, `pip install -e ".[dev]"`, set `DATABASE_URL` (PostgreSQL, or
`sqlite+aiosqlite:///./arbbot.db` for a local trial), leave `REDIS_URL` empty for a single process,
run `alembic upgrade head` (PostgreSQL only), then `python -m arbbot`.

## Telegram commands

| Command | What it does |
|---|---|
| `/start` `/help` | Initialise, list commands |
| `/dashboard` | Bankroll, free and deployed capital per exchange, trade counts, gross/fees/net, day/week/month returns, drawdown |
| `/status` | Exchange and database health, book freshness, eligibility checks, startup reconciliation |
| `/scan` | Refresh books and scan now |
| `/opportunities` | Qualifying opportunities, plus recent rejections and the reason |
| `/positions` `/close [id\|all]` | Open positions; sell both legs at the bids |
| `/balance` | Real balance, allocation, deployed, reserved, free, per exchange |
| `/profit` `/history` | Realized, locked-in and mark-to-market P&L; recent executions |
| `/capital kalshi 500` | Allocation per exchange (and paper starting balances) |
| `/threshold min 1.5` | Dynamic profitability settings, with the last evaluations explained |
| `/risk` | Exposure against every limit, active pauses, recent risk events |
| `/settings [group\|name value]` | Every runtime parameter, validated and bounded |
| `/autotrade on\|off\|paper\|live` | Automatic execution and paper/live mode |
| `/pause` `/resume` | Pause new trades; resume and acknowledge risk pauses |
| `/stop` | Emergency stop: autotrade off, no new orders |
| `/mappings` | Review, approve or reject market matches; `/mappings discover` |

Only the user ids in `TELEGRAM_ADMIN_IDS` can use the bot.

Alerts are sent for new opportunities, completed pairs (with every field: both orders, YES and NO
price, quantity, cost, fees, payout, expected net and return, capital, execution time, confidence),
partial fills, failed executions, emergency hedges and unwinds, exchange disconnections, risk-limit
violations, settlements and a daily summary.

## How a trade is decided

```
net = settlement payout − acquisition cost − all trading costs − risk buffer
return = net / capital actually required for both legs
```

- **Acquisition cost** comes from walking both ask ladders, never from displayed or last prices.
- **Trading costs**: each exchange's fee model, plus currency conversion on stablecoin capital.
- **Risk buffer**: expected slippage (order latency × recent volatility × book staleness), leg risk
  (probability one leg fails × cost of unwinding the other), settlement uncertainty (scaled up as match
  confidence drops), operational risk, and the cost of capital locked until settlement.
- **Dynamic threshold**: the required return slides between `min_roi_pct` and `max_roi_pct` on a risk
  score built from failure probability, staleness, thin depth, volatility and time to settlement. Deep,
  fresh, stable books clear a small margin; thin or jumpy ones need a large one.
- **Hard rejects** regardless of return: unverified match, slippage over the limit, failure probability
  over the limit, liquidity under the minimum, stale books.
- **Size**: the size with the highest net profit that clears the threshold, then cut down by fractional
  Kelly and every hard limit (per trade, per exchange free capital, total exposure, per-market
  concentration, max unmatched leg, share of visible depth).

Every evaluation with a gross edge is stored in `opportunities`, including rejections and why.

## Matching markets

1. **Semantic candidates**: TF-IDF similarity over normalised titles and outcome labels.
2. **Deterministic validation**: dates, numeric thresholds, comparison direction, outcome polarity
   (including inverted pairs), close times in UTC, settlement sources, voiding terms. A failed check
   cannot be overridden by a score.
3. **Settlement-rule verification**: text similarity cannot prove two contracts settle the same way,
   so heuristic confidence is capped at 90. A pair becomes tradeable (≥ 98 and rule-compatible) only when
   - Claude compares both rule sets clause by clause (`ANTHROPIC_API_KEY`, capped at 99), or
   - you approve it in Telegram after reading both (`/mappings approve`, recorded as 100 with your name).

Mappings are stored in PostgreSQL and rechecked on every discovery run. If either exchange changes a
title, rules text, source or close time, the mapping is demoted and stops trading until re-verified.
If a hedged pair ever settles for anything other than its size, trading pauses and the mapping is rejected.

## Execution

Cross-exchange orders are not atomic, so each trade is a journaled state machine:

`opportunity_detected → opportunity_validated → capital_reserved → orders_submitted →
first_leg_partially_filled | first_leg_fully_filled → second_leg_partially_filled → both_legs_matched →
emergency_hedge → emergency_unwind → completed | failed`

- Books are refetched and the trade re-priced and re-sized immediately before any order.
- Legs go one after the other, the leg more likely to fail first. If it does not fill, there is no exposure.
- The second leg is sized to exactly what the first leg filled.
- An unmatched remainder is hedged only while that loses less than unwinding and stays within
  `max_hedge_loss_pct`; otherwise the excess first leg is sold. Time-boxed by `max_unhedged_seconds`.
- Anything still unhedged is recorded as a naked position, trading pauses, and you are alerted.
- Every order is written to the journal with its idempotency key before it is sent. Orders are never
  resent blindly: if an acknowledgement is lost, the fill is determined from the account's positions.
- On restart the bot restores its configuration, cancels unexpected resting orders, finishes any
  execution the journal shows in flight, and compares exchange positions with the journal before scanning.

## Risk controls

Limits (all in `/settings risk`): bankroll exposure, trade size, daily loss, drawdown, open positions,
unmatched exposure and time, execution time, slippage, minimum liquidity, market concentration.

New trading pauses automatically when an exchange API is down, books go stale, a balance cannot be
verified, the daily loss or drawdown limit is hit, an execution or fee discrepancy occurs, a settlement
does not pay as a hedge, or exchange positions differ from the journal. Outage-type pauses clear
themselves; the rest wait for `/resume`. `/stop` is the global kill switch.

A hedged position is not treated as risk-free: it counts against exposure and concentration, carries a
settlement buffer, and Kelly sizing assumes a non-zero chance it fails to settle as a hedge.

## Paper trading, backtesting, going live

Paper mode uses live order books and simulates fills against them with latency, random failures and
partial fills. The paper venue has no access to any real order method.

```bash
arbbot-backtest --synthetic 3 --steps 900 --latency 0.4 --fail-prob 0.05 --partial-prob 0.2
arbbot-backtest --books var/books/2026-11-14.jsonl.gz     # replay recorded books for your verified pairs
```

The bot records order books for watched markets to `DATA_DIR/books/` so you build your own history.

`/autotrade live` shows the unlock checklist. Live stays locked until:
paper period ≥ `paper_min_days`, ≥ `paper_min_trades` matched, paper P&L ≥ `paper_min_net_pnl`, failure
rate ≤ `paper_max_fail_rate_pct`, capital allocated on both exchanges, no active risk pause, and both
adapters pass eligibility. Then `/autotrade live CONFIRM`, then `/autotrade on`.

## Exchange access and compliance

Each adapter runs `check_eligibility()` at startup and before live unlock: credentials present,
exchange reachable and trading, authenticated account access, order API permission, plus your own
attestation (`*_ELIGIBILITY_CONFIRMED`) that your jurisdiction, account type and the exchange's API
terms allow automated trading. That part cannot be checked by code, so read the current rules first.

- **Kalshi**: official Trade API v2 with RSA-PSS or Ed25519 key signing. `KALSHI_ENV=demo` points at the sandbox.
- **Polymarket US** (`POLYMARKET_VENUE=us`): the regulated US exchange, via the official `polymarket-us` SDK.
- **Polymarket international** (`intl`): not open to US persons. The adapter calls Polymarket's own
  geoblock endpoint and disables trading when it reports blocked. It does not work around that.

If an exchange is unavailable to you, set it to off. The rest keeps running, and paper trading still
works on whatever public data is reachable. You cannot arbitrage with one exchange.

Funding and withdrawals are yours to do on each exchange. The bot treats the two balances as separate
pools and never assumes money moves between them.

## Verified here, and not

**Test suite** (`pytest`, 60 tests, also run against PostgreSQL 16 and Redis 7): fee and profit math,
dynamic threshold, sizing and limits, matcher validation, every execution state (partial fills,
emergency hedge, emergency unwind, unhedgeable residual, lost acknowledgement, crash recovery),
settlement and settlement discrepancy, paper/live gating, every Telegram command, restart persistence,
rate limiting, and adapter request/response mapping.

**Against the live exchanges (public market data, no account):**

- Discovery read every open market on both venues: about 112,000 on Kalshi and 64,000 on Polymarket
  US, almost all sports props. Matching them takes about 40 seconds, and a full discovery run about 10 minutes.
- Matching found real cross-venue equivalents (for example the Week 10 "most rushing yards" props and
  the Copa do Brasil winner markets) and correctly kept them below the trading bar until verified.
  It also exposed a near miss worth knowing about: Kalshi lists both a per-game and a whole-week
  "most rushing yards" market for the same player, and Polymarket US titles some markets with only the
  player's name. The bot now builds those titles from the market's own rules, but this is exactly why
  nothing trades without settlement-rule verification.
- Read real order books on both venues and ran them through the profitability engine. At the time of
  testing the matched pairs cost $1.02 to $1.07 per $1 payout: no arbitrage, which is the normal state.
  Expect opportunities to be brief and rare.
- **Rate limits are tight.** Kalshi returned HTTP 429 during the full event crawl and the public
  Polymarket US gateway allowed roughly 20 requests a minute from the test machine (a shared IP, so
  yours may be higher). The bot paces reads, halves its rate on every 429, polls each exchange in its
  own loop, and only polls a few unverified candidates (`/settings max_watched_candidates`). With API keys
  it streams books over WebSocket on both venues, which removes most of the polling.

**Not verified: anything that needs an account.** Before real money, check these on Kalshi demo and
with minimum-size orders:

- Kalshi: the fields of a filled order (`taker_fill_cost_dollars`, `taker_fees_dollars`) for NO-side buys
  and for sells; WebSocket message field names (REST polling is the safety net and books are always
  refetched before an order).
- Polymarket US: that short-side limit prices are quoted at the long price
  (`POLYMARKET_US_SHORT_PRICE_CONVENTION`); the fee model (markets publish a `feeCoefficient`, modelled as
  coefficient × contracts × p × (1−p), and the bot reads the real commission back from every order);
  the authenticated market-data WebSocket.
- Polymarket international: order precision on fine-tick markets.

The fee-discrepancy check pauses trading if an exchange charges more than the model expected.

## Honest expectations

Fees take a real bite out of a thin edge, both exchanges are separate pots of money you have to fund
ahead of time, capital stays locked until markets settle, and other bots are after the same gaps.
Paper trade it first and only put in money you can afford to lose.

## Development

```bash
pip install -e ".[dev,intl]"
pytest                                   # SQLite + in-process cache
TEST_DATABASE_URL=postgresql+asyncpg://arbbot:arbbot@localhost/arbbot_test \
TEST_REDIS_URL=redis://localhost:6379/0 pytest
ruff check arbbot tests
alembic revision --autogenerate -m "change"   # after editing database/models.py
```
