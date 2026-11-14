"""Intelligent market matching.

Three stages, in increasing strictness:

1. Semantic candidate generation: TF-IDF cosine similarity over normalised
   titles and outcome labels.
2. Deterministic validation: dates, numeric thresholds, comparison direction,
   outcome polarity, close times (normalised to UTC), settlement sources and
   voiding conditions. A failed deterministic check can never be overridden.
3. Settlement-rule verification: text heuristics cannot establish that two
   contracts resolve identically, so a mapping only becomes tradeable after a
   rule-by-rule comparison by the LLM verifier (if configured) or an explicit
   operator approval in Telegram.

Heuristic-only confidence is capped at 90. Automated execution requires >= 98
and settlement_compatible, which only stage 3 can grant.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol

from ..config.runtime import RuntimeConfig
from ..database.repo import Repository, rules_hash
from . import events
from .events import EventBus
from .types import Market

log = logging.getLogger(__name__)

HEURISTIC_CAP = 90.0
LLM_CAP = 99.0

STOP = set("a an the of in on at to for by be is are was will would with and or as it its this that than "
           "from before after any which who what when how do does market contract resolve resolves "
           "resolution yes no if s".split())
SYNONYMS = {
    "btc": "bitcoin", "eth": "ethereum", "sol": "solana", "fed": "federal reserve", "fomc": "federal reserve",
    "u.s.": "us", "u.s": "us", "usa": "us", "united states": "us", "gop": "republican", "dem": "democrat",
    "dems": "democrat", "democratic": "democrat", "republicans": "republican", "democrats": "democrat",
    "pres": "president", "presidential": "president", "vs.": "vs", "versus": "vs", "nyc": "new york city",
    "cpi": "inflation", "gdp": "gdp", "wins": "win", "won": "win", "winner": "win", "beats": "win", "beat": "win",
    "defeat": "win", "defeats": "win", "hits": "reach", "hit": "reach", "reaches": "reach", "exceeds": "above",
    "exceed": "above", "over": "above", "greater": "above", "higher": "above", "under": "below", "less": "below",
    "lower": "below", "interest rates": "rate", "rates": "rate",
}
MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
UP = {"above", "exceed", "at least", "or more", "or higher", "more than", ">", ">=", "reach"}
DOWN = {"below", "at most", "or less", "or lower", "fewer than", "<", "<="}
NEG = re.compile(r"\b(not|won't|will not|fail(?:s)? to|no longer|doesn't|does not|never)\b")
VOID_TERMS = {
    "postpone": "postponed", "cancel": "cancelled", "void": "void", "refund": "refund", "50-50": "split",
    "50/50": "split", "tie": "tie", "draw": "draw", "overtime": "overtime", "extra time": "overtime",
    "forfeit": "forfeit", "suspend": "suspended", "no contest": "no contest", "early close": "early close",
    "closes early": "early close", "abandon": "abandoned", "reschedul": "rescheduled",
}
SOURCES = ["associated press", "ap ", "bls", "bureau of labor statistics", "federal reserve", "bea",
           "coinbase", "cf benchmarks", "binance", "coingecko", "coinmarketcap", "nba", "nfl", "mlb", "nhl",
           "espn", "uefa", "fifa", "ufc", "atp", "wta", "nasa", "noaa", "national weather service", "cdc",
           "sec", "treasury", "white house", "congress", "supreme court", "new york times", "reuters",
           "bloomberg", "wall street journal", "fox news", "cnn", "nbc", "box office mojo", "rotten tomatoes",
           "billboard", "spotify", "oscars", "academy", "nobel", "opec", "eia", "ecb", "bank of england",
           "bank of japan", "nasdaq", "s&p", "dow jones", "chainlink", "pyth", "uma"]


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'")
    for k, v in SYNONYMS.items():
        if " " in k or "." in k:
            t = t.replace(k, v)
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    return t


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9$%.&'+-]+", normalize(text)):
        w = w.strip(".'-")
        w = SYNONYMS.get(w, w)
        for part in w.split():
            if part and part not in STOP:
                out.append(part)
    return out


def numbers(text: str) -> set[float]:
    """Numeric thresholds in a title, with k/m/b suffixes expanded. Years and day-of-month
    numbers that belong to dates are handled by dates(), not here."""
    t = normalize(text)
    t = re.sub(r"\b(?:%s)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?" % "|".join(MONTHS), " ", t)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", " ", t)
    out = set()
    for m in re.finditer(r"(?<![a-z0-9])\$?(\d+(?:\.\d+)?)\s*(k|m|mm|b|bn|t|thousand|million|billion|trillion|%|bps)?(?![a-z0-9])", t):
        v = float(m.group(1))
        suf = m.group(2) or ""
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9,
                "billion": 1e9, "t": 1e12, "trillion": 1e12}.get(suf, 1)
        v *= mult
        if suf == "" and v.is_integer() and 1900 <= v <= 2100:
            continue  # a year
        out.add(round(v, 6))
    return out


def dates(text: str) -> set[tuple[int, int, int]]:
    """(year, month, day) mentions; 0 where unspecified."""
    t = normalize(text)
    out = set()
    for m in re.finditer(r"\b(%s)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?" % "|".join(MONTHS), t):
        out.add((int(m.group(3) or 0), MONTHS[m.group(1)], int(m.group(2))))
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        out.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"\b(?:in|by|before|during|end of)\s+(%s)[a-z]*\.?(?:\s+(\d{4}))?\b" % "|".join(MONTHS), t):
        out.add((int(m.group(2) or 0), MONTHS[m.group(1)], 0))
    return out


def years(text: str) -> set[int]:
    return {int(y) for y in re.findall(r"\b(20\d{2})\b", text)}


def direction(text: str) -> int:
    t = " " + normalize(text) + " "
    up = any(f" {w} " in t or (not w.isalpha() and w in t) for w in UP)
    down = any(f" {w} " in t or (not w.isalpha() and w in t) for w in DOWN)
    return 1 if up and not down else -1 if down and not up else 0


def void_terms(text: str) -> set[str]:
    t = text.lower()
    return {v for k, v in VOID_TERMS.items() if k in t}


def sources(m: Market) -> set[str]:
    t = f" {m.resolution_source} {m.rules} ".lower()
    found = {s.strip() for s in SOURCES if s in t}
    found |= {d.lower().removeprefix("www.") for d in re.findall(r"https?://([a-z0-9.-]+)", t)}
    return found


def _dates_compatible(a: set, b: set) -> bool:
    if not a or not b:
        return True
    for ya, ma, da in a:
        for yb, mb, db in b:
            if ma == mb and (da == db or 0 in (da, db)) and (ya == yb or 0 in (ya, yb)):
                return True
    return False


@dataclass(slots=True)
class MatchResult:
    confidence: float
    inverted: bool
    deterministic_ok: bool
    settlement_compatible: bool = False
    method: str = "heuristic"
    checks: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


class RuleVerifier(Protocol):
    name: str

    async def verify(self, kalshi: Market, poly: Market, heuristic: MatchResult) -> "VerifierVerdict | None": ...


@dataclass(slots=True)
class VerifierVerdict:
    equivalent: bool
    inverted: bool
    confidence: float
    settlement_compatible: bool
    differences: list[str]
    reasoning: str


class MarketMatcher:
    def __init__(self, cfg: RuntimeConfig, repo: Repository, bus: EventBus, verifier: RuleVerifier | None = None,
                 *, top_k: int = 2, min_similarity: float = 0.45, verify_above: float = 70.0,
                 max_postings: int = 400):
        self._structural_ids: set[str] = set()
        self.cfg, self.repo, self.bus, self.verifier = cfg, repo, bus, verifier
        self.top_k, self.min_similarity, self.verify_above = top_k, min_similarity, verify_above
        self.max_postings = max_postings

    # -- stage 1: semantic candidates ---------------------------------------
    @staticmethod
    def _doc(m: Market) -> list[str]:
        label = "" if m.yes_label.lower() in ("yes", "") else m.yes_label
        return tokens(f"{m.title} {m.subtitle} {label}")

    def candidates(self, kalshi: list[Market], poly: list[Market]) -> list[tuple[Market, Market, float]]:
        """TF-IDF cosine over an inverted index, blocked by close date.

        Two contracts can only be the same if they close around the same time, so the index is
        keyed by (close day, token) and a market only looks at days within the close-time
        tolerance. Tokens that appear in a huge number of same-day markets ("total", "over",
        "win") carry almost no information and are skipped, which keeps this fast on
        100k+ markets.
        """
        docs_p = [self._doc(m) for m in poly]
        docs_k = [self._doc(m) for m in kalshi]
        df: Counter = Counter()
        for d in docs_p + docs_k:
            df.update(set(d))
        n = len(docs_p) + len(docs_k)
        idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}

        def vec(doc: list[str]) -> dict[str, float]:
            tf = Counter(doc)
            v = {t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in tf.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            return {t: x / norm for t, x in v.items()}

        def day(m: Market) -> int:
            return int(m.close_time.timestamp() // 86400) if m.close_time else -1

        span = int(math.ceil(self.cfg.close_time_tolerance_hours / 24)) + 1
        index: dict[tuple[int, str], list[tuple[int, float]]] = defaultdict(list)
        for i, d in enumerate(docs_p):
            dd = day(poly[i])
            for t, w in vec(d).items():
                index[(dd, t)].append((i, w))
        out = []
        for k, dk in zip(kalshi, docs_k):
            kd = day(k)
            days = [-1] if kd < 0 else range(kd - span, kd + span + 1)
            scores: dict[int, float] = defaultdict(float)
            for t, w in vec(dk).items():
                for dd in days:
                    post = index.get((dd, t))
                    if not post or len(post) > self.max_postings:
                        continue
                    for i, wp in post:
                        scores[i] += w * wp
            if not scores:
                continue
            best = sorted(scores.items(), key=lambda kv: -kv[1])[: self.top_k]
            out.extend((k, poly[i], sc) for i, sc in best if sc >= self.min_similarity)
        return out

    def similarity(self, k: Market, p: Market) -> float:
        """Cosine similarity of one pair (tf weighting only; used outside discovery)."""
        a, b = Counter(self._doc(k)), Counter(self._doc(p))
        dot = sum(a[t] * b[t] for t in a.keys() & b.keys())
        na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    # -- stage 2: deterministic validation -----------------------------------
    def assess(self, k: Market, p: Market, similarity: float | None = None) -> MatchResult:
        cfg = self.cfg
        if similarity is None:
            similarity = self.similarity(k, p)
        checks: dict = {"similarity": round(similarity, 4)}
        fails: list[str] = []
        tk, tp = f"{k.title} {k.subtitle}", f"{p.title} {p.subtitle}"

        # close / expiration times (both already UTC)
        if k.close_time and p.close_time:
            gap_h = abs((k.close_time - p.close_time).total_seconds()) / 3600
            checks["close_gap_hours"] = round(gap_h, 2)
            if gap_h > cfg.close_time_tolerance_hours:
                fails.append(f"close times differ by {gap_h:.0f}h")
        else:
            checks["close_gap_hours"] = None
            fails.append("missing close time")

        dk, dp = dates(tk), dates(tp)
        checks["dates"] = [sorted(dk), sorted(dp)]
        if not _dates_compatible(dk, dp):
            fails.append("dates in titles differ")
        yk, yp = years(tk), years(tp)
        if yk and yp and not (yk & yp):
            fails.append(f"years differ: {sorted(yk)} vs {sorted(yp)}")

        nk, np_ = numbers(f"{tk} {k.yes_label}"), numbers(f"{tp} {p.yes_label}")
        checks["numbers"] = [sorted(nk), sorted(np_)]
        if nk != np_:
            fails.append(f"numeric thresholds differ: {sorted(nk)} vs {sorted(np_)}")

        # polarity
        inverted = False
        named_k = k.yes_label.lower() not in ("yes", "")
        named_p = p.yes_label.lower() not in ("yes", "")
        if named_k or named_p:
            lk = set(tokens(k.yes_label)) if named_k else set()
            same = len(lk & set(tokens(p.yes_label))) if named_p else len(lk & set(tokens(p.title)))
            opp = len(lk & set(tokens(p.no_label))) if p.no_label.lower() not in ("no", "") else 0
            checks["label_overlap"] = {"same": same, "opposite": opp}
            if named_k and named_p:
                if same > opp and same > 0:
                    inverted = False
                elif opp > same:
                    inverted = True
                else:
                    fails.append("cannot align outcome labels")
            elif same == 0:
                fails.append("named outcome not found in the other market")
        dir_k, dir_p = direction(tk + " " + k.yes_label), direction(tp + " " + p.yes_label)
        checks["direction"] = [dir_k, dir_p]
        if dir_k * dir_p < 0:
            inverted = not inverted
            checks["direction_inverted"] = True
        elif (dir_k == 0) != (dir_p == 0) and nk:
            fails.append("comparison direction stated in only one market")
        if bool(NEG.search(normalize(tk))) != bool(NEG.search(normalize(tp))):
            inverted = not inverted
            checks["negation_inverted"] = True

        # settlement sources and voiding conditions
        sk, sp = sources(k), sources(p)
        checks["sources"] = [sorted(sk), sorted(sp)]
        checks["source_match"] = bool(sk & sp) if sk and sp else None
        vk, vp_ = void_terms(k.rules), void_terms(p.rules)
        checks["void_terms"] = [sorted(vk), sorted(vp_)]
        checks["void_terms_match"] = vk == vp_
        checks["early_close"] = [k.can_close_early, p.can_close_early]

        a, b = set(self._doc(k)), set(self._doc(p))
        jacc = len(a & b) / len(a | b) if a | b else 0.0
        checks["jaccard"] = round(jacc, 4)
        score = 55 * min(similarity, 1.0) + 20 * jacc
        score += 8 if checks["close_gap_hours"] is not None and checks["close_gap_hours"] <= 2 else 4
        score += 7 if checks["source_match"] else 0
        score += 5 if checks["void_terms_match"] else 0
        score += 5 if dk and dp else 2
        if fails:
            score = min(score, 50.0)
        return MatchResult(round(min(score, HEURISTIC_CAP), 2), inverted, not fails, False, "heuristic", checks, fails)

    # -- stage 3 + persistence ------------------------------------------------
    async def evaluate_pair(self, k: Market, p: Market, similarity: float | None = None) -> tuple[int, MatchResult]:
        existing = await self.repo.get_mapping_row(k.market_id, p.market_id)
        if existing and existing.status in ("verified", "rejected") and \
                existing.kalshi_rules_hash == rules_hash(k) and existing.polymarket_rules_hash == rules_hash(p):
            res = MatchResult(existing.confidence, existing.inverted, True, existing.settlement_compatible,
                              existing.method, existing.checks or {})
            return existing.id, res
        res = self.assess(k, p, similarity)
        status = "candidate"
        if res.deterministic_ok and self.verifier and res.confidence >= self.verify_above:
            try:
                verdict = await self.verifier.verify(k, p, res)
            except Exception:
                log.exception("rule verifier failed")
                verdict = None
            if verdict:
                res.method = self.verifier.name
                res.checks["verifier"] = {"equivalent": verdict.equivalent, "confidence": verdict.confidence,
                                          "settlement_compatible": verdict.settlement_compatible,
                                          "differences": verdict.differences, "reasoning": verdict.reasoning[:1500]}
                if not verdict.equivalent:
                    res.confidence, status = min(res.confidence, 30.0), "rejected"
                elif verdict.inverted != res.inverted:
                    # the two methods disagree about which side is which: never trade this automatically
                    res.failures.append("polarity disagreement between heuristic and verifier")
                    res.deterministic_ok, res.confidence = False, min(res.confidence, 50.0)
                else:
                    res.confidence = min(verdict.confidence, LLM_CAP)
                    res.settlement_compatible = verdict.settlement_compatible
                    if res.settlement_compatible and res.confidence >= self.cfg.min_match_confidence:
                        status = "verified"
        if not res.deterministic_ok and status == "candidate" and res.confidence < 40:
            status = "rejected"
        res.checks["failures"] = res.failures
        mid = await self.repo.save_mapping(k, p, inverted=res.inverted, confidence=res.confidence,
                                           settlement_compatible=res.settlement_compatible, status=status,
                                           method=res.method, checks=res.checks)
        if status == "verified" or (existing is None and status == "candidate" and res.deterministic_ok):
            await self.bus.publish(events.MAPPING_CHANGED, mapping_id=mid, status=status, confidence=res.confidence,
                                   kalshi=k.title, polymarket=p.title, new=existing is None)
        return mid, res

    async def run(self, kalshi: list[Market], poly: list[Market],
                  prepare: "Callable[[Market], Awaitable[Market]] | None" = None) -> dict[str, int]:
        """Screen every candidate pair in memory; only pairs that pass deterministic validation
        (or that are already stored) touch the database or the rule verifier."""
        counts = {"candidates": 0, "verified": 0, "rejected": 0, "screened": 0}
        structural = await self.save_structural(kalshi, poly)
        for k, v in structural.items():
            counts[k] += v
        taken = set(self._structural_ids)
        kalshi = [m for m in kalshi if m.market_id not in taken]
        poly = [m for m in poly if m.market_id not in taken]
        known = {(r.kalshi_id, r.polymarket_id) for r in await self.repo.mapping_rows(
            ("verified", "candidate", "stale", "rejected"))}
        pairs = await asyncio.to_thread(self.candidates, kalshi, poly)
        for k, p, sim in pairs:
            counts["screened"] += 1
            if (k.market_id, p.market_id) not in known and not self.assess(k, p, sim).deterministic_ok:
                counts["rejected"] += 1
                continue
            if prepare:
                p = await prepare(p)
            _, res = await self.evaluate_pair(k, p, sim)
            if res.settlement_compatible and res.confidence >= self.cfg.min_match_confidence:
                counts["verified"] += 1
            elif res.deterministic_ok:
                counts["candidates"] += 1
            else:
                counts["rejected"] += 1
        return counts

    async def save_structural(self, kalshi: list[Market], poly: list[Market]) -> dict[str, int]:
        """Crypto price contracts are matched field by field (asset, index, averaging, time,
        strike, rounding). A pair that passes every check is settlement-compatible; it becomes
        tradeable automatically only when auto_verify_structural is on."""
        from .crypto_matcher import match_crypto
        counts = {"candidates": 0, "verified": 0, "rejected": 0}
        self._structural_ids = set()
        for cm in match_crypto(kalshi, poly):
            self._structural_ids |= {cm.kalshi.market_id, cm.poly.market_id}
            existing = await self.repo.get_mapping_row(cm.kalshi.market_id, cm.poly.market_id)
            if existing and existing.status == "rejected":
                continue
            if cm.compatible:
                status = "verified" if (self.cfg.auto_verify_structural or (existing and existing.status == "verified")) \
                    else "candidate"
            else:
                status = "rejected"
            mid = await self.repo.save_mapping(cm.kalshi, cm.poly, inverted=False,
                                               confidence=99.0 if cm.compatible else 40.0,
                                               settlement_compatible=cm.compatible, status=status,
                                               method="crypto-structural", checks=cm.checks)
            counts["verified" if status == "verified" else "candidates" if cm.compatible else "rejected"] += 1
            if existing is None and status == "verified":
                await self.bus.publish(events.MAPPING_CHANGED, mapping_id=mid, status=status, confidence=99.0,
                                       kalshi=cm.kalshi.title, polymarket=cm.poly.title, new=True)
        return counts

    # -- operator decisions -----------------------------------------------------
    async def approve(self, mapping_id: int, operator: str, inverted: bool | None = None) -> tuple[bool, str]:
        m = await self.repo.get_mapping(mapping_id)
        if m is None:
            return False, "mapping not found"
        res = self.assess(m.kalshi, m.polymarket)
        # The operator has read both rule sets and may accept differences in wording or trading
        # hours. Contracts about different years are never the same contract.
        hard = [f for f in res.failures if "years differ" in f]
        if hard:
            return False, "deterministic validation failed and cannot be overridden: " + "; ".join(hard)
        checks = dict(m.checks or {})
        checks["approved_by"] = operator
        checks["approved_at"] = datetime.now(timezone.utc).isoformat()
        checks["overridden_failures"] = res.failures
        await self.repo.save_mapping(m.kalshi, m.polymarket, inverted=m.inverted if inverted is None else inverted,
                                     confidence=100.0, settlement_compatible=True, status="verified",
                                     method="manual", checks=checks)
        return True, "verified"

    async def reject(self, mapping_id: int) -> None:
        await self.repo.update_mapping(mapping_id, status="rejected", settlement_compatible=False)

    # -- periodic revalidation -------------------------------------------------
    async def revalidate(self, fresh: dict[str, Market]) -> list[int]:
        """Re-check verified mappings against freshly fetched markets (keyed by Market.key).
        Any change to rules text, title, source or close time makes the match uncertain:
        the mapping is demoted and trading on it stops until it is verified again."""
        demoted = []
        for row in await self.repo.mapping_rows(("verified",)):
            k, p = fresh.get(f"kalshi:{row.kalshi_id}"), fresh.get(f"polymarket:{row.polymarket_id}")
            reason = ""
            if k is None or p is None:
                continue  # market left discovery (closed/settled); positions are tracked separately
            if rules_hash(k) != row.kalshi_rules_hash or rules_hash(p) != row.polymarket_rules_hash:
                reason = "market terms changed since verification"
            elif row.method == "crypto-structural":
                from .crypto_matcher import compare, parse_kalshi, parse_poly
                kc, pc = parse_kalshi(k), parse_poly(p)
                cm = compare(k, kc, p, pc) if kc and pc else None
                if cm is None or not cm.compatible:
                    reason = "; ".join(cm.reasons) if cm else "contract no longer parses as a crypto threshold"
            elif row.method != "manual":
                # operator approvals already accepted any deterministic differences; for those,
                # only a change in the contracts themselves (above) reopens the question
                res = self.assess(k, p)
                hard = [f for f in res.failures if "close times" in f or "years differ" in f]
                if hard:
                    reason = "; ".join(hard)
            if reason:
                await self.repo.update_mapping(row.id, status="stale", settlement_compatible=False,
                                               confidence=min(row.confidence, 50.0))
                demoted.append(row.id)
                await self.bus.publish(events.RISK_ALERT, kind="matching_uncertain", severity="warning",
                                       message=f"mapping #{row.id} ({k.title}) demoted: {reason}", paused=False,
                                       data={"mapping_id": row.id})
            else:
                await self.repo.update_mapping(row.id, validated_at=datetime.now(timezone.utc))
        return demoted
