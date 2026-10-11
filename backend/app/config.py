"""Application settings. All secrets are read from backend environment variables only."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# A small, liquid NSE test universe (trading symbols). Instrument keys are NOT
# hard-coded: they are resolved from Upstox's published instrument master.
DEFAULT_UNIVERSE = (
    "RELIANCE,TCS,HDFCBANK,ICICIBANK,INFY,SBIN,BHARTIARTL,ITC,"
    "LT,KOTAKBANK,AXISBANK,HINDUNILVR,BAJFINANCE,MARUTI,SUNPHARMA,TATASTEEL"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # "demo" serves clearly labelled synthetic data; "upstox" uses the live Upstox API.
    data_mode: Literal["demo", "upstox"] = "demo"

    upstox_api_key: str = ""
    upstox_api_secret: SecretStr = SecretStr("")
    upstox_redirect_uri: str = "http://localhost:8000/api/auth/upstox/callback"
    # Optional: a token generated elsewhere. Normally obtained via the OAuth login flow.
    upstox_access_token: SecretStr = SecretStr("")
    upstox_base_url: str = "https://api.upstox.com"
    upstox_instruments_url: str = (
        "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"
    )

    # NVIDIA NIM (OpenAI-compatible) for plain-language explanations of computed signals.
    nvidia_api_key: SecretStr = SecretStr("")
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nvidia_max_requests_per_minute: int = 30

    # Automatic alerts during market hours.
    alerts_enabled: bool = True
    alert_move_levels: str = "2,4"  # day-change % levels, alerted once per day each way
    alert_fast_move_pct: float = 1.0  # move within the window below
    alert_fast_window_minutes: int = 15
    alert_cooldown_minutes: int = 30
    alert_signal_timeframe: Literal["5m", "15m", "1h", "1d"] = "15m"
    # Optional: also send alerts to Telegram (create a bot with @BotFather).
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    # Live news (Google News RSS), scored by the NVIDIA model with a keyword fallback.
    news_enabled: bool = True
    news_poll_minutes: float = 5.0
    news_lookback_hours: int = 48
    # Cap on how far news can move the technical up-probability, in percentage points.
    news_max_shift_pts: float = 10.0

    # Optional. When empty an in-process TTL cache is used.
    redis_url: str = ""

    frontend_origin: str = "http://localhost:3000"
    universe: str = DEFAULT_UNIVERSE

    quote_poll_seconds: float = 5.0
    # A quote whose last trade is older than this (during market hours) is labelled stale.
    stale_after_seconds: int = 180

    # Client-side throttle, deliberately below Upstox's published limits.
    # Verify current limits at https://upstox.com/developer/api-documentation/
    upstox_max_requests_per_second: int = 10
    upstox_max_requests_per_minute: int = 250

    data_dir: Path = BACKEND_DIR / "data"

    @property
    def universe_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.universe.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
