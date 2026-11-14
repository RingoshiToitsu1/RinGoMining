"""Taker fee estimation. Estimates are deliberately rounded up: the execution
engine records the fee each exchange actually charged and the risk manager
flags an execution discrepancy when reality exceeds the estimate."""
from __future__ import annotations

from decimal import Decimal

from .types import CENT, ONE, ZERO, FeeSpec, ceil_to

_SUBCENT = Decimal("0.0001")


def estimate_fee(spec: FeeSpec, price: Decimal, size: Decimal) -> Decimal:
    if size <= 0 or spec.kind == "none" or spec.rate <= 0:
        return ZERO
    p = min(max(price, ZERO), ONE)
    if spec.kind == "kalshi_quadratic":
        return ceil_to(spec.rate * size * p * (ONE - p), CENT)
    if spec.kind == "poly_exponent":
        base = p * (ONE - p)
        if spec.exponent != ONE:
            base = Decimal(str(float(base) ** float(spec.exponent)))
        return ceil_to(size * spec.rate * base, _SUBCENT)
    if spec.kind == "notional_bps":
        return ceil_to(size * p * spec.rate, _SUBCENT)
    if spec.kind == "flat_per_contract":
        return ceil_to(size * spec.rate, _SUBCENT)
    raise ValueError(f"unknown fee kind {spec.kind!r}")


def fee_for_fills(spec: FeeSpec, fills: list[tuple[Decimal, Decimal]]) -> Decimal:
    """Fee for a sweep across several price levels [(price, size), ...]."""
    return sum((estimate_fee(spec, p, s) for p, s in fills), ZERO)
