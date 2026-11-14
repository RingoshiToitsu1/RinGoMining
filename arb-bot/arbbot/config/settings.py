"""Static, deploy-time settings read from the environment (.env).

Secrets and infrastructure endpoints live here. Everything a trader tunes at
runtime (bankroll, thresholds, risk limits) lives in RuntimeConfig, persisted
in PostgreSQL and editable from Telegram.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- infrastructure -------------------------------------------------
    database_url: str = "postgresql+asyncpg://arbbot:arbbot@localhost:5432/arbbot"
    redis_url: str = ""            # empty -> in-process cache/locks (single instance only)
    data_dir: Path = Path("./var")
    log_level: str = "INFO"

    # --- telegram -------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_admin_ids: str = ""   # comma separated numeric user ids

    # --- kalshi ---------------------------------------------------------
    kalshi_enabled: bool = True
    kalshi_env: str = "prod"       # prod | demo
    kalshi_base_url: str = ""      # override; defaults by env
    kalshi_ws_url: str = ""
    kalshi_api_key_id: str = ""
    kalshi_private_key_path: str = ""
    kalshi_private_key_pem: str = ""
    kalshi_reads_per_second: float = 8.0
    # You attest that your account, jurisdiction and API agreement permit automated trading.
    kalshi_eligibility_confirmed: bool = False

    # --- polymarket -----------------------------------------------------
    # us:   Polymarket US (CFTC-regulated exchange, api.polymarket.us)
    # intl: international CLOB on Polygon. Blocked for US persons; the adapter
    #       refuses to trade if Polymarket's own geoblock check says blocked.
    # off:  adapter disabled
    polymarket_venue: str = "us"
    polymarket_eligibility_confirmed: bool = False
    polymarket_us_key_id: str = ""
    polymarket_us_secret_key: str = ""
    polymarket_reads_per_second: float = 10.0
    polymarket_us_api_url: str = "https://api.polymarket.us"
    polymarket_us_gateway_url: str = "https://gateway.polymarket.us"
    # Fallback taker fee (fraction of notional) used until the venue reports the real rate.
    polymarket_us_taker_fee_rate: float = 0.005
    # How BUY_SHORT limit prices are quoted: "long" (price of the YES side) or "own".
    polymarket_us_short_price_convention: str = "long"

    polymarket_private_key: str = ""
    polymarket_funder: str = ""
    polymarket_signature_type: int = 0
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    polymarket_geoblock_url: str = "https://polymarket.com/api/geoblock"
    polymarket_chain_id: int = 137

    # --- optional LLM rule verifier --------------------------------------
    anthropic_api_key: str = ""
    matcher_llm_model: str = "claude-opus-5-5"

    # --- discovery -------------------------------------------------------
    # crypto: only BTC/ETH/SOL/XRP daily price contracts (fast, few requests); all: every open market
    discovery_scope: str = "crypto"
    crypto_assets: str = "BTC,ETH,SOL,XRP"
    # screen: cheap bulk top-of-book quotes, full books only where a gap shows (rate-limit friendly)
    # books:  poll full order books for every watched pair
    scan_mode: str = "screen"
    discovery_max_days_to_close: int = Field(default=120, ge=1)
    discovery_min_volume: float = 0.0

    @property
    def admin_ids(self) -> set[int]:
        return {int(x) for x in self.telegram_admin_ids.replace(" ", "").split(",") if x}

    @property
    def crypto_asset_set(self) -> set[str]:
        return {x.strip().upper() for x in self.crypto_assets.split(",") if x.strip()}

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def kalshi_private_key(self) -> str:
        if self.kalshi_private_key_pem:
            return self.kalshi_private_key_pem.replace("\\n", "\n")
        if self.kalshi_private_key_path:
            return Path(self.kalshi_private_key_path).read_text()
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
