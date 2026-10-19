"""Forex headlines: the news engine with currency searches and a pair-aware scoring prompt.

A headline is positive when it should lift the pair's price, i.e. strengthen the foreign currency against
the rupee (USDINR up = the rupee weakens). Stored apart from stock news.
"""

import math
from datetime import timedelta

from app.forex import PAIR_INFO
from app.market_session import now_ist
from app.news import GOOGLE_NEWS_URL, NewsService, parse_rss

# Google News search, and words a headline must contain to count for the pair.
FX_NEWS = {
    "USDINR": ('(rupee OR INR) (dollar OR Fed OR RBI OR forex)', ["rupee", "inr", "usd/inr", "dollar index", "rbi", "fed", "forex"]),
    "EURINR": ('(rupee OR INR OR euro) (euro OR ECB)', ["euro", "eur/inr", "ecb", "eurozone"]),
    "GBPINR": ('(rupee OR INR OR pound) (pound OR sterling OR "Bank of England")', ["pound", "sterling", "gbp", "bank of england"]),
    "JPYINR": ('(rupee OR INR OR yen) (yen OR "Bank of Japan" OR BoJ)', ["yen", "jpy", "bank of japan", "boj"]),
}

FX_SCORE_PROMPT = """You rate financial headlines for one Indian-rupee currency pair, e.g. USD/INR (rupees per US dollar).
For each numbered headline return how it is likely to move THIS PAIR's price over the next few days.
Return ONLY a JSON array, one object per headline, in order:
[{"i": 1, "sentiment": -1.0 to 1.0, "impact": "low" | "medium" | "high", "reason": "max 12 words"}]
sentiment: +1 = the pair likely RISES (the foreign currency strengthens or the rupee weakens),
-1 = the pair likely FALLS (the rupee strengthens or the foreign currency weakens), 0 = neutral or unrelated.
impact: high = central-bank decisions (RBI, Fed, ECB, BoE, BoJ), RBI intervention, inflation or jobs data, big capital flows;
low = generic commentary. Do not invent facts beyond the headline."""


class ForexNews(NewsService):
    score_prompt = FX_SCORE_PROMPT

    def subject(self, symbol: str, name: str) -> str:
        info = PAIR_INFO.get(symbol, {})
        return f"Currency pair: {info.get('base', symbol[:3])}/INR (price = rupees per {info.get('per', 'unit')})"

    def fallback_score(self, title: str) -> dict:
        # Stock keywords ("falls", "gains") say nothing about which way a currency pair moves: stay neutral.
        return {"sentiment": 0.0, "impact": "low", "reason": "not scored (AI scoring unavailable)", "scored_by": "keywords"}

    async def fetch(self, symbol: str, name: str) -> list[dict]:
        query, words = FX_NEWS[symbol]
        days = max(1, math.ceil(self.settings.news_lookback_hours / 24))
        params = {"q": f"{query} when:{days}d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
        resp = await self.http.get(GOOGLE_NEWS_URL, params=params, timeout=15,
                                   headers={"User-Agent": "Mozilla/5.0 (AI-Stock research tool)"})
        resp.raise_for_status()
        since = now_ist() - timedelta(hours=self.settings.news_lookback_hours)
        return parse_rss(resp.text, symbol, [symbol, *words], since)
