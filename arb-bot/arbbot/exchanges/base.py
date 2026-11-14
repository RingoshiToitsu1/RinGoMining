"""Exchange adapter contract. The arbitrage engine only ever talks to this
interface, so either exchange integration can be replaced independently."""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Awaitable, Callable

from ..core.types import (Balance, EligibilityReport, Exchange, Market, OrderBook, OrderRequest,
                          OrderResult, Outcome, Resolution)

BookCallback = Callable[[OrderBook], Awaitable[None]]


class ExchangeError(Exception):
    """The exchange answered and refused (4xx, validation, insufficient funds)."""


class ExchangeUnavailable(ExchangeError):
    """Transport failure or 5xx: the outcome of the request may be unknown."""


class TradingDisabled(ExchangeError):
    """Adapter is not permitted to place orders (eligibility, paper mode, kill switch)."""


class ExchangeAdapter(ABC):
    name: Exchange
    supports_streaming: bool = False
    is_simulated: bool = False
    # round-trip currency conversion cost applied to capital on this venue (bps)
    conversion_bps: float = 0.0

    # -- lifecycle ---------------------------------------------------------
    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @abstractmethod
    async def check_eligibility(self) -> EligibilityReport:
        """Verify credentials, account permissions, jurisdiction and API access.
        Trading stays disabled unless every check passes."""

    @abstractmethod
    async def health(self) -> bool: ...

    # -- market data ---------------------------------------------------------
    @abstractmethod
    async def list_markets(self) -> list[Market]: ...

    @abstractmethod
    async def get_market(self, market_id: str) -> Market | None: ...

    @abstractmethod
    async def get_orderbook(self, market: Market) -> OrderBook: ...

    async def get_orderbooks(self, markets: list[Market]) -> dict[str, OrderBook]:
        out = {}
        for m in markets:
            out[m.market_id] = await self.get_orderbook(m)
        return out

    async def stream_orderbooks(self, markets: list[Market], on_book: BookCallback) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_resolution(self, market: Market) -> tuple[Resolution, Decimal] | None:
        """None while unresolved, else (resolution, payout per YES contract)."""

    # -- account ---------------------------------------------------------------
    @abstractmethod
    async def get_balance(self) -> Balance: ...

    @abstractmethod
    async def get_positions(self) -> dict[tuple[str, Outcome], Decimal]:
        """{(market_id, outcome): contracts held}"""

    @abstractmethod
    async def get_open_orders(self) -> list[dict]:
        """Resting orders: [{'order_id', 'market_id'}]. The bot only sends IOC/FOK,
        so anything resting is unexpected and gets cancelled at reconciliation."""

    # -- trading ---------------------------------------------------------------
    @abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderResult:
        """Submit an immediate (IOC/FOK) limit order and return its terminal state.
        Must raise ExchangeUnavailable when the outcome is unknown."""

    @abstractmethod
    async def get_order(self, order_id: str, market: Market) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, order_id: str, market: Market) -> None: ...

    async def on_position_settled(self, market: Market, outcome: Outcome, size: Decimal,
                                  payout: Decimal) -> None:
        """Real exchanges credit settlements themselves; simulated ones override this."""
        return None
