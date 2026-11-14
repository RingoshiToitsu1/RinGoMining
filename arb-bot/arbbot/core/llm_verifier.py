"""Optional settlement-rule verifier backed by Claude.

Reads both contracts' full resolution rules and returns a structured verdict.
It can only ever *add* a requirement: deterministic validation must already
have passed, and its confidence is capped below a manual approval.
"""
from __future__ import annotations

import logging

import anthropic
from pydantic import BaseModel, Field

from .market_matcher import MatchResult, VerifierVerdict
from .types import Market

log = logging.getLogger(__name__)

SYSTEM = """You verify whether two prediction-market contracts on different exchanges are economically \
equivalent, for a cross-exchange arbitrage system that will buy YES on one and NO on the other.

A false "equivalent" loses real money, so be strict. The contracts are equivalent only if, in every \
realistic scenario, contract A's YES pays out exactly when contract B's YES pays out (or exactly when \
B's NO pays out: then set inverted=true).

Compare, rule by rule: the underlying event; the exact threshold and comparison (>, >=); the measurement \
time, time zone and deadline; the settlement source or oracle; what happens on cancellation, postponement, \
ties, early resolution, data revisions or source unavailability; and whether either can be voided or \
resolved 50-50 where the other would not.

settlement_compatible must be false if any scenario you can identify would make the two settle \
differently, or if either contract's rules are too vague to tell. List every material difference. \
confidence is your 0-100 probability that a YES/NO hedge across the two always pays exactly one side."""


class Verdict(BaseModel):
    equivalent: bool
    inverted: bool = Field(description="true if A's YES corresponds to B's NO")
    confidence: float = Field(description="0-100")
    settlement_compatible: bool
    differences: list[str]
    reasoning: str


def _describe(tag: str, m: Market) -> str:
    return (f"<contract id=\"{tag}\" exchange=\"{m.exchange.value}\">\n"
            f"title: {m.title}\nsubtitle: {m.subtitle}\nYES outcome label: {m.yes_label}\n"
            f"NO outcome label: {m.no_label}\ncategory: {m.category}\n"
            f"trading closes (UTC): {m.close_time}\nlatest settlement (UTC): {m.expiration_time}\n"
            f"can close early: {m.can_close_early}\nsettlement source: {m.resolution_source}\n"
            f"rules:\n{m.rules}\n</contract>")


class ClaudeRuleVerifier:
    name = "llm"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def verify(self, kalshi: Market, poly: Market, heuristic: MatchResult) -> VerifierVerdict | None:
        prompt = (f"{_describe('A', kalshi)}\n\n{_describe('B', poly)}\n\n"
                  "Are contracts A and B equivalent for a YES/NO hedge?")
        try:
            resp = await self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM,
                output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_format=Verdict,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.RateLimitError:
            log.warning("verifier rate limited; mapping stays unverified")
            return None
        except anthropic.APIStatusError as e:
            log.warning("verifier API error %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.warning("verifier unreachable; mapping stays unverified")
            return None
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return None
        v = resp.parsed_output
        return VerifierVerdict(v.equivalent, v.inverted, max(0.0, min(v.confidence, 100.0)),
                               v.settlement_compatible and v.equivalent, v.differences, v.reasoning)
