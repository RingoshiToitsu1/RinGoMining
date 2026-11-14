"""Shared formatting helpers for Telegram (HTML parse mode)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from html import escape
from typing import Any


@dataclass
class Reply:
    text: str
    # rows of (label, "command args") buttons; pressing one runs that command
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)


def usd(x: Any, sign: bool = False) -> str:
    v = Decimal(str(x or 0))
    s = f"{abs(v):,.2f}"
    if sign:
        return ("+" if v >= 0 else "-") + "$" + s
    return ("-" if v < 0 else "") + "$" + s


def pct(x: Any, digits: int = 2) -> str:
    return f"{float(x or 0):.{digits}f}%"


def e(text: Any, limit: int = 90) -> str:
    t = str(text)
    return escape(t if len(t) <= limit else t[: limit - 1] + "…")


def onoff(v: bool) -> str:
    return "ON" if v else "OFF"


def mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m ago"
    return f"{seconds / 3600:.1f}h ago"
