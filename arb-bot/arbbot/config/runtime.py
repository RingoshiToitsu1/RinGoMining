"""Runtime trading configuration: persisted in the database, editable from
Telegram, restored on restart. Every field is validated and bounded."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    # --- operating state --------------------------------------------------
    mode: str = Field("paper", pattern="^(paper|live)$", description="paper or live")
    autotrade: bool = Field(False, description="execute qualifying opportunities automatically")
    paused: bool = Field(False, description="manual pause of new trades")
    emergency_stop: bool = Field(False, description="global kill switch")
    live_unlocked: bool = Field(False, description="live trading explicitly unlocked by the operator")

    # --- capital (each exchange is a separate pool; nothing moves between them) ---
    kalshi_allocation: Decimal = Field(Decimal("0"), ge=0, description="max USD the bot may use on Kalshi")
    polymarket_allocation: Decimal = Field(Decimal("0"), ge=0, description="max USD the bot may use on Polymarket")
    paper_kalshi_balance: Decimal = Field(Decimal("1000"), ge=0, description="starting paper balance, Kalshi")
    paper_polymarket_balance: Decimal = Field(Decimal("1000"), ge=0, description="starting paper balance, Polymarket")

    # --- dynamic profitability threshold (percent of capital deployed) ---------
    min_roi_pct: float = Field(1.0, ge=0.1, le=50, description="lowest net return ever accepted (best quality)")
    max_roi_pct: float = Field(8.0, ge=0.2, le=100, description="net return required at worst acceptable quality")
    min_annualized_pct: float = Field(0.0, ge=0, le=1000, description="optional floor on annualised net return")
    min_net_profit_usd: Decimal = Field(Decimal("0.25"), ge=0, description="ignore trades netting less than this")

    # --- position sizing ----------------------------------------------------
    kelly_fraction: float = Field(0.25, gt=0, le=1, description="fraction of full Kelly")
    loss_given_failure: float = Field(0.5, gt=0, le=1, description="capital fraction lost if a hedge fails to settle as a hedge")
    settlement_tail_prob: float = Field(0.005, ge=0, le=0.2, description="residual failure probability even at 100 confidence")
    max_trade_usd: Decimal = Field(Decimal("200"), gt=0, description="hard cap on capital per trade")
    max_trade_bankroll_pct: float = Field(10.0, gt=0, le=100, description="hard cap per trade, percent of bankroll")
    min_trade_usd: Decimal = Field(Decimal("5"), ge=0, description="skip trades smaller than this")
    max_book_take_pct: float = Field(50.0, gt=0, le=100, description="max share of visible profitable depth to take")

    # --- risk limits ----------------------------------------------------------
    max_exposure_pct: float = Field(80.0, gt=0, le=100, description="max bankroll deployed at once")
    max_daily_loss_usd: Decimal = Field(Decimal("50"), ge=0, description="pause after losing this much in a UTC day")
    max_drawdown_pct: float = Field(10.0, gt=0, le=100, description="pause at this peak-to-trough equity decline")
    max_open_positions: int = Field(20, ge=1, le=1000)
    max_unhedged_usd: Decimal = Field(Decimal("100"), ge=0, description="max capital in a single unmatched first leg")
    max_unhedged_seconds: float = Field(20.0, gt=0, le=600, description="max time an unmatched leg may stay open")
    max_execution_seconds: float = Field(45.0, gt=0, le=900, description="max time for a whole execution")
    max_slippage_pct: float = Field(1.0, ge=0, le=20, description="reject if expected slippage exceeds this")
    min_liquidity_usd: Decimal = Field(Decimal("50"), ge=0, description="min executable notional per leg")
    max_market_concentration_pct: float = Field(25.0, gt=0, le=100, description="max bankroll in one market pair")
    max_book_age_ms: int = Field(3000, ge=100, le=120000, description="order books older than this are stale")
    max_exec_fail_prob: float = Field(0.35, gt=0, le=1, description="reject above this modelled leg-failure probability")
    max_hedge_loss_pct: float = Field(3.0, ge=0, le=50, description="worst loss per contract accepted to complete a hedge")
    unwind_floor_pct: float = Field(50.0, ge=0, le=100, description="never dump an unmatched leg below this share of entry price")
    fee_discrepancy_tolerance_pct: float = Field(25.0, ge=0, le=500, description="alert when real fees exceed estimates by this")

    # --- cost model -------------------------------------------------------------
    kalshi_latency_ms: int = Field(250, ge=0, le=10000)
    polymarket_latency_ms: int = Field(400, ge=0, le=10000)
    base_slippage_bps: float = Field(5.0, ge=0, le=500)
    base_leg_fail_prob: float = Field(0.03, ge=0, le=1)
    usdc_conversion_bps: float = Field(10.0, ge=0, le=500, description="round-trip USD<->USDC cost on Polymarket international capital")
    capital_cost_apr_pct: float = Field(5.0, ge=0, le=100, description="opportunity cost of capital locked until settlement")
    settlement_risk_bps: float = Field(25.0, ge=0, le=2000)
    operational_risk_bps: float = Field(10.0, ge=0, le=2000)

    # --- matching -------------------------------------------------------------
    auto_verify_structural: bool = Field(False, description="treat contracts whose rules match field by field (crypto price contracts) as verified without manual approval")
    min_match_confidence: float = Field(98.0, ge=98, le=100, description="confidence required for automated execution")
    close_time_tolerance_hours: float = Field(36.0, ge=0, le=720)
    revalidate_hours: float = Field(6.0, gt=0, le=168)

    # --- loops -------------------------------------------------------------
    scan_interval_s: float = Field(2.0, ge=0.2, le=300)
    book_poll_interval_s: float = Field(1.5, ge=0.2, le=60)
    discovery_interval_s: float = Field(900.0, ge=30, le=86400)
    settlement_check_interval_s: float = Field(300.0, ge=10, le=86400)
    max_watched_candidates: int = Field(10, ge=0, le=500, description="unverified pairs whose books are polled for review")
    screen_interval_s: float = Field(4.0, ge=0.5, le=300, description="seconds between bulk quote screens")
    screen_max_deep: int = Field(6, ge=1, le=100, description="max pairs per screen that get full order books")
    notify_opportunities: bool = True
    daily_summary_hour_utc: int = Field(0, ge=0, le=23)

    # --- paper trading: simulation and live-unlock requirements ------------------
    paper_fail_prob: float = Field(0.05, ge=0, le=1, description="simulated order failure probability")
    paper_partial_prob: float = Field(0.15, ge=0, le=1, description="simulated partial-fill probability")
    paper_min_days: float = Field(7.0, ge=0, le=365, description="paper period required before live")
    paper_min_trades: int = Field(30, ge=0, le=100000)
    paper_min_net_pnl: Decimal = Field(Decimal("0"), description="paper net P&L required before live")
    paper_max_fail_rate_pct: float = Field(25.0, ge=0, le=100)

    # ------------------------------------------------------------------
    @property
    def bankroll(self) -> Decimal:
        return self.kalshi_allocation + self.polymarket_allocation

    def set_value(self, key: str, raw: str) -> Any:
        """Parse and validate a single field from a Telegram command."""
        if key not in type(self).model_fields:
            raise KeyError(key)
        ann = type(self).model_fields[key].annotation
        value: Any = raw
        if ann is bool:
            low = raw.strip().lower()
            if low not in {"on", "off", "true", "false", "1", "0", "yes", "no"}:
                raise ValueError("expected on/off")
            value = low in {"on", "true", "1", "yes"}
        setattr(self, key, value)  # validate_assignment coerces and bounds-checks
        self._cross_validate()
        return getattr(self, key)

    def _cross_validate(self) -> None:
        if self.max_roi_pct < self.min_roi_pct:
            raise ValueError("max_roi_pct must be >= min_roi_pct")

    def describe(self, key: str) -> str:
        return type(self).model_fields[key].description or ""


# Fields that must never be set through the generic /settings command.
PROTECTED_FIELDS = {"mode", "live_unlocked", "emergency_stop", "autotrade", "paused"}
