"""Structural matcher for crypto price-threshold contracts (BTC, ETH, SOL, XRP).

Kalshi `KXBTCD`/`KXETHD`/`KXSOLD`/`KXXRPD` and Polymarket US `cpc*-above-day-*` contracts both pay
on CF Benchmarks' Real-Time Index, averaged over the 60 seconds before a fixed time. The two
venues differ only in how the strike edge is written:

    Polymarket  YES  iff  round(avg, N) >= X        i.e. avg >= X - 0.5 * 10**-N
    Kalshi      YES  iff  avg > F                   (F is just under X, e.g. 65249.99 for $65,250)

Between those two edges the venues disagree, and a cross-venue hedge could lose on both legs.
Each pair is accepted only if that window is tiny, and the window and an estimate of the
probability of landing in it are recorded with the mapping.

Every check here is deterministic and read from the contracts' own fields and rule text: asset,
price index, averaging method, settlement instant, strike, comparison and rounding.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .types import D, Market

ET = ZoneInfo("America/New_York")

ASSETS = {
    "BTC": {"kalshi_series": "KXBTCD", "index": ("BRTI", "Bitcoin Real-Time Index")},
    "ETH": {"kalshi_series": "KXETHD", "index": ("ERTI", "ETHUSD_RTI", "Ethereum Real-Time Index")},
    "SOL": {"kalshi_series": "KXSOLD", "index": ("SOLUSD_RTI", "Solana Real-Time Index")},
    "XRP": {"kalshi_series": "KXXRPD", "index": ("XRPUSD_RTI", "Ripple-Dollar Real Time Index", "XRP Real-Time Index")},
}
SERIES_ASSET = {v["kalshi_series"]: k for k, v in ASSETS.items()}

POLY_SLUG = re.compile(r"^cpc(?:eth|sol|xrp)?-(btc|eth|sol|xrp)-above-day-(\d{4})-(\d{2})-(\d{2})-([0-9p]+)$")
POLY_TIME = re.compile(r"at (\d{1,2}):(\d{2})\s*([AP]M) ET on ([A-Z][a-z]{2,8} \d{1,2}, \d{4})")
ROUNDING = re.compile(r"rounded to the nearest (\d+) decimal places?")

# Conservative daily volatility used to turn the disagreement window into a probability.
DAILY_VOL = 0.03
MAX_MISMATCH_PROB = 0.002


@dataclass(slots=True)
class CryptoContract:
    asset: str
    instant: datetime          # settlement instant (UTC)
    strike: Decimal            # Polymarket: X (>=); Kalshi: F (>)
    index_ok: bool
    method_ok: bool
    rounding: int | None = None


@dataclass(slots=True)
class CryptoMatch:
    kalshi: Market
    poly: Market
    compatible: bool
    checks: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def _mentions_index(text: str, asset: str) -> bool:
    return "CF Benchmarks" in text and any(tag.lower() in text.lower() for tag in ASSETS[asset]["index"])


def _sixty_second_average(text: str) -> bool:
    t = text.lower()
    return ("60" in t or "sixty" in t) and "average" in t


def parse_poly(m: Market) -> CryptoContract | None:
    s = POLY_SLUG.match(m.market_id)
    if not s:
        return None
    asset = s.group(1).upper()
    strike = D(s.group(5).replace("p", "."))
    t = POLY_TIME.search(m.rules)
    if not t:
        return None
    hour = int(t.group(1)) % 12 + (12 if t.group(3) == "PM" else 0)
    day = datetime.strptime(t.group(4), "%b %d, %Y") if len(t.group(4).split()[0]) == 3 else \
        datetime.strptime(t.group(4), "%B %d, %Y")
    instant = datetime(day.year, day.month, day.day, hour, int(t.group(2)), tzinfo=ET).astimezone(ZoneInfo("UTC"))
    r = ROUNDING.search(m.rules)
    text_ok = "at or above" in m.rules or "greater than or equal" in m.rules
    return CryptoContract(asset, instant, strike, _mentions_index(m.rules, asset),
                          _sixty_second_average(m.rules) and text_ok, int(r.group(1)) if r else None)


def parse_kalshi(m: Market) -> CryptoContract | None:
    asset = SERIES_ASSET.get(m.extra.get("series_ticker", ""))
    if not asset or m.extra.get("strike_type") != "greater" or m.extra.get("floor_strike") is None:
        return None
    if not m.close_time:
        return None
    return CryptoContract(asset, m.close_time, D(m.extra["floor_strike"]), _mentions_index(m.rules, asset),
                          _sixty_second_average(m.rules) and "above" in m.rules)


def compare(k: Market, kc: CryptoContract, p: Market, pc: CryptoContract) -> CryptoMatch:
    reasons: list[str] = []
    if kc.asset != pc.asset:
        reasons.append("different asset")
    if kc.instant != pc.instant:
        reasons.append(f"settlement times differ ({kc.instant:%Y-%m-%d %H:%M} vs {pc.instant:%Y-%m-%d %H:%M} UTC)")
    if not (kc.index_ok and pc.index_ok):
        reasons.append("price index not confirmed as the same CF Benchmarks index on both")
    if not (kc.method_ok and pc.method_ok):
        reasons.append("60-second average / comparison wording not found on both")
    if pc.rounding is None:
        reasons.append("Polymarket rounding rule not found")
    width = prob = None
    if pc.rounding is not None:
        poly_edge = pc.strike - D(5) * D(10) ** -(pc.rounding + 1)
        width = abs(poly_edge - kc.strike)
        rel = float(width / pc.strike) if pc.strike > 0 else 1.0
        prob = rel / (DAILY_VOL * math.sqrt(2 * math.pi))
        if kc.strike >= pc.strike:
            reasons.append("Kalshi strike is not just under the Polymarket strike")
        elif prob > MAX_MISMATCH_PROB:
            reasons.append(f"strike edges differ by {width} ({prob:.2%} worst-case chance the venues disagree)")
    checks = {
        "method": "crypto-structural", "asset": kc.asset, "settles_utc": pc.instant.isoformat(),
        "poly_strike_gte": str(pc.strike), "kalshi_strike_gt": str(kc.strike), "poly_rounding_dp": pc.rounding,
        "disagreement_window": str(width) if width is not None else None,
        "disagreement_prob_max": round(prob, 6) if prob is not None else None,
        "index": ASSETS[kc.asset]["index"][0], "failures": reasons,
    }
    return CryptoMatch(k, p, not reasons, checks, reasons)


def match_crypto(kalshi: list[Market], poly: list[Market], assets: set[str] | None = None) -> list[CryptoMatch]:
    """Pair every Polymarket daily threshold contract with the Kalshi contract on the same
    asset, same settlement instant and the nearest strike just below it."""
    by_key: dict[tuple[str, datetime], list[tuple[Market, CryptoContract]]] = {}
    for k in kalshi:
        kc = parse_kalshi(k)
        if kc and (not assets or kc.asset in assets):
            by_key.setdefault((kc.asset, kc.instant), []).append((k, kc))
    out = []
    for p in poly:
        pc = parse_poly(p)
        if not pc or (assets and pc.asset not in assets):
            continue
        cands = [(k, kc) for k, kc in by_key.get((pc.asset, pc.instant), ()) if kc.strike < pc.strike]
        if not cands:
            continue
        k, kc = max(cands, key=lambda x: x[1].strike)
        if (pc.strike - kc.strike) / pc.strike > D("0.001"):
            continue                       # nearest Kalshi strike is a different level
        out.append(compare(k, kc, p, pc))
    return out
