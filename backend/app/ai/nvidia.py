"""NVIDIA NIM (Nemotron) explanations of already-computed signals.

The model only rephrases structured data produced by the backend. It never generates
prices, indicators or probabilities, and every number in its answer is checked
against the source data before it is shown.
"""

import json
import logging
import re

import httpx

from app.cache import Cache
from app.config import Settings
from app.market_session import now_ist
from app.providers.base import ProviderError
from app.ratelimit import RateLimiter

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You explain technical-analysis output for an Indian equities research tool.
You receive one JSON object. Explain ONLY what is in it.

Rules:
- Never invent or estimate any number, price, level, indicator value, news, event, date or probability. Every number you write must appear in the JSON.
- Do not forecast prices and do not tell the reader to buy, sell or hold. The signal is a rule-based technical score, not a prediction.
- Use the precomputed "price_position" and "bar_unit" fields for comparisons and periods (e.g. "20-bar EMA" on hourly candles). Do not compare numbers yourself.
- If signal.status is "no_trade", say the tool marks it NO TRADE and explain the listed reasons.
- If a scenario exists, describe it as a volatility-based scenario, not a target that will be reached. If it is null, do not mention it.
- Mention the listed risks. If data_source is "demo", say the data is synthetic.
- Do not end with advice, suggestions or statements about what traders should do. Stop after the risks.
- Plain English for a retail investor. 120-180 words, three short paragraphs: (1) the signal and its main drivers, (2) what the indicators show, (3) risks and limits. No headings, no bullet points, no markdown."""

# Grouped numbers (1,241.90 / 1,23,456) or plain ones; a trailing comma is never captured.
NUMBER_RE = re.compile(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?")
BAR_UNIT = {"1d": "day", "1h": "hour", "5m": "5-minute bar", "15m": "15-minute bar", "1m": "1-minute bar"}


def _numbers(text: str) -> list[float]:
    out = []
    for m in NUMBER_RE.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def unverified_numbers(answer: str, facts: dict) -> list[str]:
    """Numbers in the answer that don't match any number in the facts (after rounding)."""
    # Every digit run in the facts counts as known, including ones inside keys like "ema200".
    known = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", json.dumps(facts))]
    bad = []
    for raw in NUMBER_RE.findall(answer):
        try:
            v = abs(float(raw.replace(",", "")))
        except ValueError:
            continue
        if v.is_integer() and v <= 10:  # counts like "three paragraphs", "2 risks"
            continue
        if not any(abs(v - k) <= max(0.051, abs(k) * 0.005) for k in known):
            bad.append(raw)
    return bad


def build_facts(detail: dict) -> dict:
    inst, sig, quote = detail["instrument"], detail["signal"], detail.get("quote") or {}
    r2 = lambda v: round(v, 2) if isinstance(v, (int, float)) else v  # noqa: E731
    price = quote.get("last_price") or detail.get("last_close")
    snap = detail["snapshot"]
    position = {}
    if price is not None:
        for key in ("ema9", "ema20", "ema50", "ema200", "vwap", "bb_upper", "bb_mid", "bb_lower"):
            if snap.get(key) is not None:
                position[key] = "above" if price > snap[key] else "below"
    return {
        "data_source": detail["source"],
        "symbol": inst["symbol"],
        "company": inst["name"],
        "sector": inst.get("sector"),
        "candle_timeframe": detail["timeframe"],
        "bar_unit": BAR_UNIT[detail["timeframe"]],
        "last_completed_candle": sig.get("last_candle_time"),
        "completed_candles": sig.get("bars"),
        "quote": {
            "last_price": r2(price),
            "change_pct": r2(quote.get("change_pct")),
            "freshness": quote.get("freshness", "unavailable"),
        },
        "signal": {
            "status": sig["status"],
            "label": sig["signal"],
            "score_out_of_100": sig["score"],
            "no_trade_reasons": sig["no_trade_reasons"],
            "evidence": [{"factor": e["factor"], "detail": e["detail"], "contribution": e["score"]} for e in sig["evidence"]],
            "risks": sig["risks"],
            "scenario": sig.get("scenario"),
            "support": r2(sig["levels"].get("support")),
            "resistance": r2(sig["levels"].get("resistance")),
        },
        "indicators": {k: r2(v) for k, v in snap.items() if v is not None},
        "price_position": position,
    }


class NvidiaExplainer:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, cache: Cache) -> None:
        self._settings = settings
        self._http = http
        self._cache = cache
        self._limiter = RateLimiter(2, settings.nvidia_max_requests_per_minute)

    @property
    def configured(self) -> bool:
        return bool(self._settings.nvidia_api_key.get_secret_value())

    @property
    def model(self) -> str:
        return self._settings.nvidia_model

    async def explain(self, detail: dict) -> dict:
        if not self.configured:
            raise ProviderError("nvidia_not_configured", "NVIDIA_API_KEY is not set on the backend.", 503)
        facts = build_facts(detail)
        sig = facts["signal"]
        cache_key = (
            f"explain:{self.model}:{detail['source']}:{facts['symbol']}:{facts['candle_timeframe']}:"
            f"{facts['last_completed_candle']}:{sig['score_out_of_100']}:{sig['status']}:"
            # The text quotes the live price and its position vs the averages, so those are part of the key.
            f"{facts['quote']['last_price']}:{facts['quote']['freshness']}"
        )
        cached = await self._cache.get(cache_key)
        if cached:
            return {**cached, "cached": True}

        await self._limiter.acquire()
        try:
            resp = await self._http.post(
                f"{self._settings.nvidia_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._settings.nvidia_api_key.get_secret_value()}", "Accept": "application/json"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(facts, default=str)},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 600,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                timeout=90,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("nvidia_unreachable", f"NVIDIA API request failed: {exc.__class__.__name__}") from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise ProviderError("nvidia_auth_failed", "NVIDIA rejected the API key.", 502)
        if resp.status_code == 429:
            raise ProviderError("nvidia_rate_limited", "NVIDIA rate limit reached; try again in a minute.", 503)
        if resp.status_code != 200:
            log.warning("NVIDIA error %s: %s", resp.status_code, resp.text[:300])
            raise ProviderError("nvidia_error", f"NVIDIA API returned HTTP {resp.status_code}.")

        text = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        if not text:
            raise ProviderError("nvidia_empty", "NVIDIA returned an empty explanation.")

        result = {
            "status": "ok",
            "text": text,
            "model": self.model,
            "generated_at": now_ist().isoformat(),
            "unverified_numbers": unverified_numbers(text, facts),
            "cached": False,
        }
        await self._cache.set(cache_key, result, 3600)
        return result
