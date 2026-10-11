"""Live news: fetch headlines per stock, score them, and adjust the technical up-probability.

Pipeline (runs in the background, independent of any open browser):
  fetch    Google News RSS search per stock (Indian edition), every NEWS_POLL_MINUTES
  score    NVIDIA model rates each new headline: sentiment -1..+1 and impact low/medium/high
           (finance keyword list when no key is configured or the model fails)
  store    SQLite (data/news.db) with the price and technical probability at the time
  adjust   news-adjusted P(up) = technical P(up) shifted by recent, decayed news sentiment,
           capped at +/- NEWS_MAX_SHIFT_PTS percentage points
  learn    once each headline's 5-trading-day outcome is known, the size of the shift is
           re-fitted from real outcomes (live data only; demo prices are synthetic)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import sqlite3
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx

from app.config import Settings
from app.market_session import IST, is_session_open, now_ist
from app.providers.base import ProviderError
from app.ratelimit import RateLimiter
from app.sectors import NEWS_NAMES

log = logging.getLogger(__name__)

GOOGLE_NEWS_URL = "https://news.google.com/rss/search"
SOURCE_NAME = "Google News (RSS)"
IMPACT_WEIGHT = {"low": 0.5, "medium": 1.0, "high": 2.0}
HALF_LIFE_HOURS = 24.0
OUTCOME_DAYS = 5  # matches the daily model's horizon
DEFAULT_BETA = math.log(0.6 / 0.4)  # strong positive news (+1) moves a 50% stock to 60%
MIN_LEARN_SAMPLES = 60
BUY_AT, SELL_AT = 55.0, 45.0

# Extra names that headlines commonly use for a stock.
ALIASES: dict[str, list[str]] = {
    "RELIANCE": ["RIL", "Reliance"],
    "SBIN": ["SBI"],
    "HINDUNILVR": ["HUL"],
    "BHARTIARTL": ["Airtel"],
    "LT": ["L&T", "Larsen"],
    "KOTAKBANK": ["Kotak"],
    "MARUTI": ["Maruti"],
    "INFY": ["Infosys"],
    "BAJFINANCE": ["Bajaj Finance"],
    "SUNPHARMA": ["Sun Pharma"],
    "TATASTEEL": ["Tata Steel"],
    "NIFTY 50": ["Nifty", "Nifty50"],
    "BANK NIFTY": ["Bank Nifty", "Nifty Bank", "BankNifty"],
    "SENSEX": ["Sensex"],
}

POSITIVE = {
    "beats": 0.6, "beat": 0.5, "record": 0.5, "surge": 0.7, "surges": 0.7, "soars": 0.7, "jumps": 0.6, "rallies": 0.6,
    "rises": 0.4, "gains": 0.4, "up": 0.2, "upgrade": 0.7, "upgraded": 0.7, "outperform": 0.5, "buy": 0.4,
    "target raised": 0.6, "raises target": 0.6, "profit rises": 0.7, "profit jumps": 0.8, "net profit up": 0.7,
    "order": 0.4, "wins": 0.5, "bags": 0.5, "contract": 0.4, "dividend": 0.4, "bonus": 0.5, "buyback": 0.6,
    "approval": 0.5, "approves": 0.4, "expansion": 0.4, "acquires": 0.3, "strong": 0.4, "growth": 0.3, "high": 0.2,
    "rebounds": 0.5, "recovers": 0.4, "upbeat": 0.4, "partnership": 0.3, "deal": 0.3, "secures": 0.5, "launches": 0.2,
    "top pick": 0.5, "stocks to buy": 0.3, "bullish": 0.5, "all-time high": 0.5, "52-week high": 0.4, "rating upgrade": 0.7,
}
NEGATIVE = {
    "misses": -0.6, "miss": -0.5, "falls": -0.5, "fall": -0.4, "drops": -0.5, "slumps": -0.7, "plunges": -0.8,
    "crashes": -0.9, "tumbles": -0.7, "slips": -0.4, "down": -0.2, "downgrade": -0.7, "downgraded": -0.7,
    "underperform": -0.5, "sell": -0.4, "target cut": -0.6, "cuts target": -0.6, "loss": -0.6, "profit falls": -0.7,
    "profit declines": -0.7, "penalty": -0.6, "fine": -0.3, "probe": -0.6, "raid": -0.7, "fraud": -0.9, "sebi notice": -0.6,
    "resigns": -0.5, "weak": -0.4, "low": -0.2, "lawsuit": -0.5, "ban": -0.6, "default": -0.8, "strike": -0.4,
    "suspends": -0.6, "suspended": -0.6, "suspension": -0.6, "freeze": -0.5, "halts": -0.5, "halted": -0.5, "lose": -0.4,
    "loses": -0.4, "layoffs": -0.4, "warns": -0.5, "warning": -0.4, "slowdown": -0.4, "tax demand": -0.6, "cuts": -0.4,
    "recall": -0.5, "outage": -0.4, "delay": -0.3, "delays": -0.3, "pressure": -0.3, "bearish": -0.5, "52-week low": -0.4,
    "selloff": -0.6, "sell-off": -0.6, "headwinds": -0.4,
}
HIGH_IMPACT_WORDS = ("results", "q1", "q2", "q3", "q4", "profit", "merger", "acquisition", "sebi", "fraud", "raid",
                     "rbi", "buyback", "bonus", "dividend", "downgrade", "upgrade", "order worth", "crore", "suspend", "ban",
                     "penalty", "tax demand")

SCORE_PROMPT = """You rate Indian stock-market headlines for one company.
For each numbered headline return how it is likely to affect THIS company's share price over the next few days.
Return ONLY a JSON array, one object per headline, in order:
[{"i": 1, "sentiment": -1.0 to 1.0, "impact": "low" | "medium" | "high", "reason": "max 12 words"}]
sentiment: +1 clearly good for the share price, -1 clearly bad, 0 neutral or not about this company.
impact: high = results, big orders, regulatory action, M&A, rating changes; low = generic market commentary.
Do not invent facts beyond the headline."""


# ---- parsing & scoring -------------------------------------------------------
def news_id(symbol: str, title: str) -> str:
    return hashlib.sha1(f"{symbol}|{title.strip().lower()}".encode()).hexdigest()[:16]


def aliases_for(symbol: str, name: str) -> list[str]:
    return [symbol, name, *ALIASES.get(symbol, [])]


def mentions(title: str, names: list[str]) -> bool:
    t = title.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(n.lower())}(?![a-z0-9])", t) for n in names if n)


def parse_rss(xml_text: str, symbol: str, names: list[str], since: datetime) -> list[dict]:
    """Google News RSS items that mention the company and are newer than `since`."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        src = item.find("source")
        publisher = (src.text or "").strip() if src is not None else ""
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].strip()
        if not title or not mentions(title, names):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(IST)
        except (TypeError, ValueError):
            continue
        if published < since:
            continue
        out.append({
            "id": news_id(symbol, title),
            "symbol": symbol,
            "title": title,
            "publisher": publisher or None,
            "link": (item.findtext("link") or "").strip(),
            "published": published.isoformat(),
        })
    return out


def keyword_score(title: str) -> dict:
    """Finance keyword fallback: sentiment in [-1, 1] and a rough impact level."""
    t = f" {title.lower()} "
    hits = []
    for words in (POSITIVE, NEGATIVE):
        for w, v in words.items():
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t):
                hits.append((w, v))
    # Prefer multi-word phrases over the single words inside them ("profit falls" over "falls").
    hits = [(w, v) for w, v in hits if not any(w != o and w in o for o, _ in hits)]
    sentiment = max(-1.0, min(1.0, sum(v for _, v in hits))) if hits else 0.0
    impact = "high" if any(k in t for k in HIGH_IMPACT_WORDS) and hits else "medium" if hits else "low"
    reason = ("keywords: " + ", ".join(w for w, _ in hits[:4])) if hits else "no clear keywords"
    return {"sentiment": round(sentiment, 2), "impact": impact, "reason": reason, "scored_by": "keywords"}


def parse_llm_scores(text: str, n: int) -> list[dict] | None:
    m = re.search(r"\[.*\]", text, flags=re.S)
    if not m:
        return None
    try:
        rows = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out: list[dict | None] = [None] * n
    for r in rows if isinstance(rows, list) else []:
        try:
            i = int(r["i"]) - 1
            s = max(-1.0, min(1.0, float(r["sentiment"])))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < n:
            impact = r.get("impact") if r.get("impact") in IMPACT_WEIGHT else "medium"
            out[i] = {"sentiment": round(s, 2), "impact": impact, "reason": str(r.get("reason") or "")[:120], "scored_by": "nvidia"}
    return out if all(out) else None


def aggregate(items: list[dict], now: datetime) -> dict:
    """Recency- and impact-weighted news sentiment in [-1, 1], damped when there is little news."""
    num = den = 0.0
    for it in items:
        if it.get("sentiment") is None:
            continue
        age_h = max(0.0, (now - datetime.fromisoformat(it["published"])).total_seconds() / 3600)
        w = IMPACT_WEIGHT.get(it.get("impact") or "medium", 1.0) * 0.5 ** (age_h / HALF_LIFE_HOURS)
        num += w * it["sentiment"]
        den += w
    if den == 0:
        return {"score": 0.0, "weight": 0.0, "label": "No recent news"}
    score = (num / den) * (1 - math.exp(-den / 2))
    label = ("Positive" if score > 0.15 else "Negative" if score < -0.15 else "Neutral")
    if abs(score) > 0.5:
        label = "Strongly " + label.lower()
    return {"score": round(score, 3), "weight": round(den, 2), "label": label}


def adjust(p_up: float, score: float, beta: float, cap_pts: float) -> float:
    """Shift the technical probability in log-odds by beta * news score, capped in % points."""
    p = min(max(p_up, 1e-4), 1 - 1e-4)
    shifted = 1 / (1 + math.exp(-(math.log(p / (1 - p)) + beta * score)))
    cap = cap_pts / 100
    return min(max(shifted, p_up - cap), p_up + cap)


def fit_beta(samples: list[tuple[float, float, float]]) -> float:
    """Offset logistic regression: outcome ~ logit(p_tech) + beta * news_x (Newton on one parameter)."""
    beta = 0.0
    for _ in range(50):
        g = h = 0.0
        for p, x, y in samples:
            p = min(max(p, 1e-4), 1 - 1e-4)
            q = 1 / (1 + math.exp(-(math.log(p / (1 - p)) + beta * x)))
            g += (q - y) * x
            h += q * (1 - q) * x * x
        h += 1.0  # ridge towards zero: weak evidence keeps the shift small
        g += beta
        step = g / h
        beta -= step
        if abs(step) < 1e-6:
            break
    return beta


# ---- storage ---------------------------------------------------------------------
class NewsStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS news (
            id TEXT PRIMARY KEY, symbol TEXT, title TEXT, publisher TEXT, link TEXT, published TEXT,
            fetched_at TEXT, sentiment REAL, impact TEXT, reason TEXT, scored_by TEXT,
            price_at REAL, p_tech REAL, source TEXT, outcome_up REAL, outcome_ret REAL)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS news_sym ON news(symbol, published)")
        self.db.commit()

    def known(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        q = f"SELECT id FROM news WHERE id IN ({','.join('?' * len(ids))})"
        return {r[0] for r in self.db.execute(q, ids)}

    def add(self, rows: list[dict]) -> None:
        cols = ("id", "symbol", "title", "publisher", "link", "published", "fetched_at", "sentiment", "impact", "reason",
                "scored_by", "price_at", "p_tech", "source")
        self.db.executemany(
            f"INSERT OR IGNORE INTO news ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [tuple(r.get(c) for c in cols) for r in rows],
        )
        self.db.commit()

    def recent(self, symbol: str | None, since: datetime, limit: int = 40) -> list[dict]:
        if symbol:
            cur = self.db.execute("SELECT * FROM news WHERE symbol=? AND published>=? ORDER BY published DESC LIMIT ?",
                                  (symbol, since.isoformat(), limit))
        else:
            cur = self.db.execute("SELECT * FROM news WHERE published>=? ORDER BY published DESC LIMIT ?", (since.isoformat(), limit))
        return [dict(r) for r in cur]

    def pending_outcomes(self, source: str) -> list[dict]:
        cur = self.db.execute("SELECT * FROM news WHERE outcome_up IS NULL AND source=? AND price_at IS NOT NULL", (source,))
        return [dict(r) for r in cur]

    def set_outcome(self, nid: str, up: float, ret: float) -> None:
        self.db.execute("UPDATE news SET outcome_up=?, outcome_ret=? WHERE id=?", (up, ret, nid))
        self.db.commit()

    def learning_samples(self, source: str) -> list[dict]:
        cur = self.db.execute("SELECT * FROM news WHERE outcome_up IS NOT NULL AND source=?", (source,))
        return [dict(r) for r in cur]

    def close(self) -> None:
        self.db.close()


# ---- service ---------------------------------------------------------------------
class NewsService:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, service, store: NewsStore) -> None:
        self.settings = settings
        self.http = http
        self.service = service  # MarketService
        self.store = store
        self.alerts = None  # AlertEngine, set by the app
        self.last_fetch: datetime | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._llm_limiter = RateLimiter(1, 20)
        self._learned: tuple[datetime, dict] | None = None

    # lifecycle
    def start(self) -> None:
        if self.settings.news_enabled and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once()
                await self.update_outcomes()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{exc.__class__.__name__}: {exc}"[:200]
                log.exception("news poll failed")
            minutes = self.settings.news_poll_minutes if is_session_open() else max(15.0, self.settings.news_poll_minutes)
            await asyncio.sleep(minutes * 60)

    # fetching
    async def fetch(self, symbol: str, name: str) -> list[dict]:
        days = max(1, math.ceil(self.settings.news_lookback_hours / 24))
        params = {"q": f'"{name}" when:{days}d', "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
        resp = await self.http.get(GOOGLE_NEWS_URL, params=params, timeout=15,
                                   headers={"User-Agent": "Mozilla/5.0 (AI-Stock research tool)"})
        resp.raise_for_status()
        since = now_ist() - timedelta(hours=self.settings.news_lookback_hours)
        return parse_rss(resp.text, symbol, aliases_for(symbol, name), since)

    async def score(self, symbol: str, name: str, titles: list[str]) -> list[dict]:
        key = self.settings.nvidia_api_key.get_secret_value()
        if key and titles:
            try:
                await self._llm_limiter.acquire()
                numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
                resp = await self.http.post(
                    f"{self.settings.nvidia_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                    json={"model": self.settings.nvidia_model, "temperature": 0, "max_tokens": 120 + 60 * len(titles),
                          "chat_template_kwargs": {"enable_thinking": False},
                          "messages": [{"role": "system", "content": SCORE_PROMPT},
                                       {"role": "user", "content": f"Company: {name} (NSE: {symbol})\n{numbered}"}]},
                    timeout=60,
                )
                if resp.status_code == 200:
                    text = resp.json()["choices"][0]["message"].get("content") or ""
                    parsed = parse_llm_scores(re.sub(r"<think>.*?</think>", "", text, flags=re.S), len(titles))
                    if parsed:
                        return parsed
                log.warning("news LLM scoring fell back to keywords (HTTP %s)", resp.status_code)
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                log.warning("news LLM scoring failed: %s", exc.__class__.__name__)
        return [keyword_score(t) for t in titles]

    async def _instruments(self) -> list:
        """Universe stocks plus the dashboard indices (index news drives intraday moves)."""
        instruments, _ = await self.service.universe()
        try:
            idx, _ = await self.service.indices()
        except Exception:  # noqa: BLE001 - index list is optional for news
            idx = []
        return instruments + idx

    async def poll_once(self) -> int:
        instruments = await self._instruments()
        added = 0
        for inst in instruments:
            name = NEWS_NAMES.get(inst.symbol) or re.sub(r"\s*\((demo)\)|\bLTD\b\.?|\bLIMITED\b", "", inst.name, flags=re.I).strip()
            try:
                items = await self.fetch(inst.symbol, name)
            except httpx.HTTPError as exc:
                self.last_error = f"{inst.symbol}: {exc.__class__.__name__}"
                continue
            seen = self.store.known([x["id"] for x in items])
            fresh = [i for i in items if i["id"] not in seen]
            if not fresh:
                continue
            scores = await self.score(inst.symbol, name, [i["title"] for i in fresh])
            quote = self.service.cached_quote(inst.instrument_key)
            p_tech = None
            try:
                o = await self.service.technical_outlook(inst, "1d")
                p_tech = o["up_pct"] / 100 if o.get("status") == "ok" else None
            except ProviderError:
                pass
            now = now_ist()
            rows = [{**i, **s, "fetched_at": now.isoformat(), "price_at": quote.last_price if quote else None,
                     "p_tech": p_tech, "source": self.service.provider.name} for i, s in zip(fresh, scores)]
            self.store.add(rows)
            added += len(rows)
            await self._maybe_alert(inst.symbol, rows, now)
            await asyncio.sleep(0.5)  # be gentle with the feed
        self.last_fetch = now_ist()
        return added

    async def _maybe_alert(self, symbol: str, rows: list[dict], now: datetime) -> None:
        if not self.alerts:
            return
        for r in rows:
            fresh = now - datetime.fromisoformat(r["published"]) < timedelta(hours=6)
            if fresh and r["impact"] == "high" and abs(r["sentiment"]) >= 0.5:
                up = r["sentiment"] > 0
                await self.alerts._emit_once(
                    f"news:{r['id']}", "up" if up else "down",
                    f"📰 {'Positive' if up else 'Negative'} high-impact news: {symbol}",
                    f"{r['title']} ({r.get('publisher') or 'news'}). Sentiment {r['sentiment']:+.1f}: {r.get('reason') or ''}",
                    symbol, None,
                )

    # learning
    async def update_outcomes(self) -> None:
        """Fill in each headline's 5-trading-day price outcome (live data only)."""
        if self.service.provider.name == "demo":
            return
        pending = self.store.pending_outcomes(self.service.provider.name)
        if not pending:
            return
        instruments = await self._instruments()
        by_sym = {i.symbol: i for i in instruments}
        frames: dict[str, list] = {}
        for row in pending:
            inst = by_sym.get(row["symbol"])
            if inst is None:
                continue
            if inst.symbol not in frames:
                try:
                    frames[inst.symbol] = await self.service.provider.candles(inst, "1d")
                except ProviderError:
                    frames[inst.symbol] = []
            candles = frames[inst.symbol]
            pub = datetime.fromisoformat(row["published"]).astimezone(IST).date()
            after = [c for c in candles if c.time.astimezone(IST).date() > pub]
            if len(after) >= OUTCOME_DAYS:
                exit_px = after[OUTCOME_DAYS - 1].close
                ret = exit_px / row["price_at"] - 1
                self.store.set_outcome(row["id"], 1.0 if ret > 0 else 0.0, round(ret * 100, 3))

    def learning(self) -> dict:
        now = now_ist()
        if self._learned and now - self._learned[0] < timedelta(minutes=30):
            return self._learned[1]
        source = self.service.provider.name
        rows = self.store.learning_samples(source) if source != "demo" else []
        samples = [(r["p_tech"] if r["p_tech"] is not None else 0.5,
                    max(-1.0, min(1.0, r["sentiment"] * IMPACT_WEIGHT.get(r["impact"], 1.0) / 2)), r["outcome_up"]) for r in rows]
        info: dict = {"samples": len(samples), "min_samples": MIN_LEARN_SAMPLES, "beta": round(DEFAULT_BETA, 3)}
        if source == "demo":
            info.update(status="demo", message="Demo prices are synthetic, so news outcomes are not learned from them.")
        elif len(samples) < MIN_LEARN_SAMPLES:
            info.update(status="default", message=f"Using the default news weight until {MIN_LEARN_SAMPLES} headlines have a "
                                                  f"{OUTCOME_DAYS}-day outcome ({len(samples)} so far). Not yet validated.")
        else:
            beta = fit_beta(samples)
            directional = [(x, y) for _, x, y in samples if abs(x) >= 0.1]
            right = sum((x > 0) == (y == 1) for x, y in directional)
            info.update(status="learned", beta=round(beta, 3),
                        direction_hit_pct=round(100 * right / len(directional), 1) if directional else None,
                        message=f"News weight fitted on {len(samples)} headlines with known {OUTCOME_DAYS}-day outcomes.")
        self._learned = (now, info)
        return info

    # API
    async def snapshot(self, inst, limit: int = 25) -> dict:
        now = now_ist()
        since = now - timedelta(hours=self.settings.news_lookback_hours)
        items = self.store.recent(inst.symbol, since, limit)
        agg = aggregate(items, now)
        learn = self.learning()
        tech = await self.service.technical_outlook(inst, "1d")
        adjusted = None
        if tech.get("status") == "ok":
            p0 = tech["up_pct"] / 100
            p1 = adjust(p0, agg["score"], learn["beta"], self.settings.news_max_shift_pts)
            up = round(100 * p1, 1)
            verdict = "BUY" if up >= BUY_AT else "SELL" if up <= SELL_AT else "HOLD"
            adjusted = {"up_pct": up, "down_pct": round(100 - up, 1), "shift_pts": round(100 * (p1 - p0), 1), "verdict": verdict}
        return {
            "symbol": inst.symbol,
            "source": SOURCE_NAME,
            "price_source": self.service.provider.name,
            "enabled": self.settings.news_enabled,
            "last_fetch": self.last_fetch.isoformat() if self.last_fetch else None,
            "error": self.last_error,
            "lookback_hours": self.settings.news_lookback_hours,
            "items": [{k: it[k] for k in ("id", "title", "publisher", "link", "published", "sentiment", "impact", "reason", "scored_by")}
                      for it in items],
            "summary": agg,
            "technical": {k: tech.get(k) for k in ("status", "up_pct", "down_pct", "verdict", "horizon_label", "message")},
            "adjusted": adjusted,
            "learning": learn,
            "max_shift_pts": self.settings.news_max_shift_pts,
            "method": "News-adjusted P(up) = the daily technical P(up) shifted in log-odds by recent news sentiment "
                      "(impact-weighted, halving every 24h), capped at ±{:g} points.".format(self.settings.news_max_shift_pts),
        }

    def feed(self, limit: int = 30) -> dict:
        now = now_ist()
        items = self.store.recent(None, now - timedelta(hours=self.settings.news_lookback_hours), limit)
        return {
            "source": SOURCE_NAME,
            "last_fetch": self.last_fetch.isoformat() if self.last_fetch else None,
            "error": self.last_error,
            "enabled": self.settings.news_enabled,
            "items": [{k: it[k] for k in ("id", "symbol", "title", "publisher", "link", "published", "sentiment", "impact", "reason", "scored_by")}
                      for it in items],
        }
