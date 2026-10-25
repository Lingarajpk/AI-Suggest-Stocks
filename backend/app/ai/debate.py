"""AI debate on one chart (a lighter TradingAgents, github.com/TauricResearch/TradingAgents, Apache-2.0).

Instead of their 20-40 LLM calls with tool loops, every number is computed by this app first and handed to
the model as one JSON "facts" pack (signal, outlook model and its reliability grade, regime, active strategies
with their evidence grades and trade-filter odds, market board, time-machine-style history). Then four calls:

  1. Bull       the strongest case FOR a long, from the facts only
  2. Bear       the case against (or for a short), rebutting the bull's points
  3. Judge      weighs both and gives a 5-step rating, a thesis, levels and what would change its mind
  4. Risk       checks the judge's plan against the risk facts: approve / reduce size / reject

Honesty rules (from TradingAgents' "verified snapshot" idea and the day-trader skill's tone):
  * the model may use only numbers in the facts; every number in every answer is checked and unmatched ones
    are listed next to the text
  * HOLD is allowed and expected when the evidence is weak or mixed (unlike TradingAgents, which discourages it)
  * the rating must be one of the five words, otherwise it is shown as REVIEW, never silently as HOLD
  * the judge's entry / stop / target must sit on the right sides and within sane ATR distances, otherwise
    the rule-based levels are shown instead, with a note
  * past debates on the same chart are settled against what price did next and shown to the judge
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

from app.ai.nvidia import NvidiaExplainer, build_facts, unverified_numbers
from app.cache import Cache
from app.market_session import now_ist

log = logging.getLogger(__name__)

RATINGS = ("STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL")
CACHE_SECONDS = 1800
HISTORY_KEEP = 20

COMMON = """You are part of a research desk for Indian stocks and indices (NSE/BSE). You receive one JSON object of
facts computed by the app. Rules:
- Use ONLY the facts. Never invent numbers, prices, news, events, dates or fundamentals. Every number you write must
  appear in the JSON.
- Weak, mixed or untested evidence is a real finding: say so plainly ("this is close to a coin flip").
- Reliability fields matter: an outlook grade of "none", evidence grade "weak" or "insufficient", or a data warning
  all lower confidence.
- If data_source is "demo", the data is synthetic; say so once.
- Plain English for a retail trader. No markdown headings."""

BULL = COMMON + """
Your role: BULL researcher. Make the strongest honest case for buying now. 3-5 short points, each tied to a
specific fact. Then one sentence: the single strongest reason. Reply as JSON:
{"points": ["...", "..."], "strongest": "..."}"""

BEAR = COMMON + """
Your role: BEAR researcher. Make the strongest honest case AGAINST buying (or for selling short). You will also
see the bull's points: rebut the weakest of them using facts. 3-5 short points, then one sentence: the single
strongest reason. Reply as JSON:
{"points": ["...", "..."], "rebuttal": "...", "strongest": "..."}"""

JUDGE = COMMON + """
Your role: JUDGE. Weigh the bull and the bear on the facts, not on who sounds surer. Choose one rating:
"STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL". Use HOLD when the evidence is weak, mixed or untested.
Give absolute price levels taken from or between the facts' price, support, resistance, ATR and scenario fields
(entry near the current price; stop beyond a level; target before the next level). Reply as JSON:
{"rating": "...", "confidence": "low|medium|high", "thesis": "2-3 sentences", "entry": number|null,
 "stop": number|null, "target": number|null, "horizon": "...", "change_my_mind": "what fact would flip this"}"""

RISK = COMMON + """
Your role: RISK MANAGER. You see the judge's plan. Check it against the risk facts (volatility, regime, data
warnings, market board, expiry, F&O ban, evidence grades, past calls). Reply as JSON:
{"verdict": "approve|reduce|reject", "notes": ["...", "..."], "size_hint": "full|half|quarter|none"}"""


def _json(text: str) -> dict | None:
    """The first JSON object in a reply (models sometimes wrap it in prose or code fences)."""
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def rating_of(raw) -> str:
    r = " ".join(str(raw or "").upper().replace("_", " ").split())
    return r if r in RATINGS else "REVIEW"


def check_levels(plan: dict, price: float | None, atr: float | None) -> list[str]:
    """Problems with the judge's levels (empty = they pass)."""
    rating = plan.get("rating")
    e, s, t = plan.get("entry"), plan.get("stop"), plan.get("target")
    if rating in ("HOLD", "REVIEW"):
        return []
    if not all(isinstance(x, (int, float)) for x in (e, s, t)):
        return ["levels missing"]
    if not price or not atr:
        return []
    issues = []
    long = rating in ("BUY", "STRONG BUY")
    if abs(e - price) > 1.0 * atr:
        issues.append("entry is more than 1 ATR from the current price")
    if long and not (s < e < t) or not long and not (t < e < s):
        issues.append("stop and target are on the wrong sides of the entry")
    dist = abs(e - s) / atr
    if not 0.3 <= dist <= 4:
        issues.append(f"stop is {dist:.1f} ATR away (allowed 0.3-4)")
    return issues


def settle(history: list[dict], price: float | None) -> list[dict]:
    """Past debates on this chart, each with what price did since (only calls at least one candle old)."""
    out = []
    for h in history:
        if price is None or not h.get("price") or h.get("rating") in (None, "REVIEW"):
            continue
        move = round(100 * (price / h["price"] - 1), 2)
        side = 1 if "BUY" in h["rating"] else -1 if "SELL" in h["rating"] else 0
        out.append({"when": h["time"], "rating": h["rating"], "price_then": h["price"], "move_since_pct": move,
                    "worked": None if side == 0 else (move * side > 0)})
    return out[-5:]


def facts_for(detail: dict, context: dict | None, past: list[dict]) -> dict:
    """The pack the models read: the explainer's facts plus outlook, regime, strategies and market context."""
    facts = build_facts(detail)
    o = detail.get("model_outlook") or {}
    test = o.get("test") or {}
    facts["outlook_model"] = {
        "verdict": o.get("verdict"), "up_probability_pct": o.get("up_pct"), "horizon": o.get("horizon_label"),
        "skill_vs_guessing_pct": test.get("skill_pct"), "reliability_grade": (test.get("grade") or {}).get("grade"),
        "top_drivers": [{"factor": d["label"], "push": d["push"]} for d in (o.get("drivers") or [])[:4]],
    }
    strat = detail.get("strategies") or {}
    facts["regime"] = {k: (strat.get("regime") or {}).get(k) for k in ("name", "higher_tf_trend", "atr_percentile")} if strat.get("regime") else None
    meta = strat.get("meta") or {}
    facts["active_strategies"] = [
        {"name": s["name"], "state": s["state"], "trade_filter_win_pct": (meta.get(s["key"]) or {}).get("win_pct")}
        for s in strat.get("strategies") or [] if s.get("state") in ("long", "short")
    ]
    hist = (detail.get("history") or {}).get("trades") or {}
    facts["signal_track_record"] = {
        side: {"trades": v.get("count"), "net_avg_pct": v.get("avg_pnl_pct"), "evidence": (v.get("evidence") or {}).get("grade")}
        for side, v in hist.items() if isinstance(v, dict) and "count" in v
    }
    dq = detail.get("data_quality") or {}
    facts["data_warnings"] = [i["text"] for i in dq.get("issues") or []]
    if context:
        facts["market_board"] = {
            "regime": (context.get("regime") or {}).get("verdict"),
            "india_vix_percentile": (context.get("vix") or {}).get("percentile_1y"),
            "breadth_above_50dma_pct": (context.get("breadth") or {}).get("above_50dma_pct"),
            "expiry_today": [e["index"] for e in context.get("expiries") or [] if e.get("is_today")],
            "in_fo_ban": facts["symbol"] in ((context.get("fo_ban") or {}).get("symbols") or []),
        }
    facts["past_calls_on_this_chart"] = past
    return facts


class Debate:
    def __init__(self, nim: NvidiaExplainer, cache: Cache) -> None:
        self.nim = nim
        self.cache = cache

    @property
    def configured(self) -> bool:
        return self.nim.configured

    async def run(self, detail: dict, context: dict | None) -> AsyncIterator[dict]:
        """Yields {"step", ...} events: facts, bull, bear, judge, risk, done (or error)."""
        sym, tf = detail["instrument"]["symbol"], detail["timeframe"]
        sig = detail["signal"]
        key = f"debate:{self.nim.model}:{detail['source']}:{sym}:{tf}:{sig.get('last_candle_time')}"
        cached = await self.cache.get(key)
        if cached:
            for ev in cached["events"]:
                yield {**ev, "cached": True}
            return
        hist_key = f"debatelog:{detail['source']}:{sym}:{tf}"
        history = await self.cache.get(hist_key) or []
        price = (detail.get("quote") or {}).get("last_price") or detail.get("last_close")
        facts = facts_for(detail, context, settle(history, price))
        events: list[dict] = [{"step": "facts", "facts": facts}]
        yield events[-1]
        blob = json.dumps(facts, default=str)

        async def ask(system: str, user: str, step: str, max_tokens: int = 700) -> dict:
            text = await self.nim.chat(system, user, max_tokens=max_tokens, temperature=0.3)
            parsed = _json(text) or {"points": [text.strip()]}
            ev = {"step": step, **parsed, "unverified_numbers": unverified_numbers(text, facts)}
            events.append(ev)
            return ev

        bull = await ask(BULL, blob, "bull")
        yield bull
        bear = await ask(BEAR, json.dumps({"facts": facts, "bull_points": bull.get("points")}, default=str), "bear")
        yield bear
        judge = await ask(JUDGE, json.dumps({"facts": facts, "bull": bull, "bear": bear}, default=str), "judge", 800)
        judge["rating"] = rating_of(judge.get("rating"))
        atr = (detail.get("snapshot") or {}).get("atr14")
        problems = check_levels(judge, price, atr)
        if problems:
            sc = sig.get("scenario") or {}
            judge["level_issues"] = problems
            judge["ai_levels"] = {k: judge.get(k) for k in ("entry", "stop", "target")}
            judge["entry"], judge["stop"], judge["target"] = (
                (sc.get("entry_low", 0) + sc.get("entry_high", 0)) / 2 if sc else None, sc.get("stop_loss"), sc.get("target"))
        yield judge
        risk = await ask(RISK, json.dumps({"facts": facts, "judge": judge}, default=str), "risk", 500)
        yield risk
        done = {"step": "done", "generated_at": now_ist().isoformat(), "model": self.nim.model}
        events.append(done)
        yield done
        await self.cache.set(key, {"events": events}, CACHE_SECONDS)
        history.append({"time": now_ist().isoformat(), "rating": judge["rating"], "price": price})
        await self.cache.set(hist_key, history[-HISTORY_KEEP:], 400 * 86400)
