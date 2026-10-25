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
    # Only someone with this key can (re)connect Upstox, so visitors can't replace the owner's token.
    admin_key: SecretStr = SecretStr("")
    # Optional: the owner's Upstox user ID (UCC). Tokens of any other Upstox account are refused.
    upstox_owner_user_id: str = ""
    # Ask Upstox for the day's token automatically (the owner approves it in the Upstox app);
    # the token is delivered to /api/auth/upstox/notifier (set as the Notifier URL on the Upstox app).
    upstox_auto_request: bool = False
    upstox_auto_request_time: str = "08:45"  # IST, trading days
    # Optional: this backend's public health URL, pinged during market hours so free hosting doesn't sleep.
    keepalive_url: str = ""
    # Web push (phone/desktop notifications). Keys are generated and stored automatically if blank.
    vapid_public_key: str = ""
    vapid_private_key: SecretStr = SecretStr("")
    push_contact: str = "mailto:alerts@ai-stock.invalid"
    # Train models at startup (turned off in tests).
    warm_up: bool = True
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

    # Site login. APP_USERS = "Name:pbkdf2_sha256.<iterations>.<salt>.<hash>,Name2:..." (python -m app.auth_users hash); empty = open site.
    app_users: str = ""
    session_secret: SecretStr = SecretStr("")
    session_cookie_secure: bool = True

    frontend_origin: str = "http://localhost:3000"
    universe: str = DEFAULT_UNIVERSE

    quote_poll_seconds: float = 2.0
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
