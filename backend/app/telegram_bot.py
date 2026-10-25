"""Telegram commands for the owner (the alert bot's chat answers back).

  /call SYMBOL    the intraday call: verdict, reason, levels, regime and the daily trend
  /quote SYMBOL   last price and change
  /desk           index calls and the strongest stock BUY / SELL calls
  /market         market board: regime vote, India VIX, breadth, leading and lagging sectors, expiry, F&O ban
  /paper          the owner device's paper account (open positions and result)
  /help

Long polling (getUpdates), so no public webhook URL is needed. Only the configured TELEGRAM_CHAT_ID gets
answers; everyone else is ignored. Plain text replies; buttons re-run a command. After OpenAlice's Telegram
connector (commands with inline buttons), reimplemented.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.providers.base import ProviderError

log = logging.getLogger(__name__)

FOOTER = "\n\nAI Stock · not investment advice"
HELP = ("Commands:\n/call SYMBOL - intraday call with levels\n/quote SYMBOL - last price\n/desk - index calls and top stocks\n"
        "/market - market board\n/paper - your paper account\n/help")


def parse(text: str) -> tuple[str, str]:
    """('/call', 'RELIANCE') from '/call@MyBot reliance' (command lower-cased, argument upper-cased)."""
    parts = (text or "").strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return "", ""
    cmd = parts[0].split("@")[0].lower()
    arg = " ".join(parts[1].upper().split()) if len(parts) > 1 else ""
    return cmd, arg


def _px(v) -> str:
    return "-" if v is None else f"{v:,.2f}"


def format_call(card: dict) -> str:
    inst = card.get("instrument") or {}
    q = card.get("quote") or {}
    lines = [f"{inst.get('symbol')} · {_px(card.get('price'))}" + (f" ({q['change_pct']:+.2f}%)" if q.get("change_pct") is not None else ""),
             f"Call: {card.get('verdict')} - {card.get('reason')}"]
    lv = card.get("levels")
    if lv:
        lines.append(f"Entry {_px(lv['entry_low'])}-{_px(lv['entry_high'])} · target {_px(lv['target'])} · stop {_px(lv['stop'])} · R:R {lv['risk_reward']}")
    ctx = []
    if card.get("regime"):
        ctx.append(card["regime"]["name"])
    d = card.get("daily")
    if d and d.get("agrees") is not None:
        ctx.append("daily trend agrees" if d["agrees"] else "AGAINST the daily trend")
    if ctx:
        lines.append(" · ".join(ctx))
    if card.get("action"):
        lines.append(f"Action: {card['action']['label']} - {card['action']['text']}")
    if card.get("risks"):
        lines.append(f"Risk: {card['risks'][0]}")
    return "\n".join(lines)


def format_desk(desk: dict) -> str:
    lines = ["Intraday desk"]
    for c in desk.get("indices") or []:
        if "error" not in c:
            lines.append(f"{c['instrument']['symbol']}: {c['verdict']} at {_px(c.get('price'))}")
    st = desk.get("stocks") or {}
    for side in ("buy", "sell"):
        rows = st.get(side) or []
        if rows:
            lines.append(f"\nTop {side.upper()}: " + ", ".join(f"{r['instrument']['symbol']} {_px(r.get('price'))}" for r in rows[:3]))
    if st:
        lines.append(f"\n{st.get('scanned', 0)} stocks scanned, {st.get('waiting', 0)} waiting.")
    return "\n".join(lines)


def format_market(m: dict) -> str:
    lines = ["Market board"]
    if m.get("regime"):
        lines.append(m["regime"]["text"])
    if m.get("vix"):
        lines.append(f"India VIX {m['vix']['level']} ({m['vix']['percentile_1y']}th percentile, {m['vix']['state']})")
    if m.get("breadth"):
        b = m["breadth"]
        lines.append(f"Breadth: {b['above_50dma_pct']}% above 50-day avg · {b['advancers']} up / {b['decliners']} down")
    secs = m.get("sectors") or []
    if secs:
        lines.append("Leading: " + ", ".join(s["sector"].replace("NIFTY ", "") for s in secs[:3]))
        lines.append("Lagging: " + ", ".join(s["sector"].replace("NIFTY ", "") for s in secs[-2:]))
    today = [e["index"] for e in m.get("expiries") or [] if e.get("is_today")]
    if today:
        lines.append("Expiry today: " + ", ".join(today))
    ban = (m.get("fo_ban") or {}).get("symbols") or []
    if ban:
        lines.append("F&O ban: " + ", ".join(ban))
    return "\n".join(lines)


def format_paper(view: dict) -> str:
    a = view["account"]
    lines = [f"Paper account: {a['equity']:,.0f} ({a['return_pct']:+.2f}%) · cash {a['cash']:,.0f} · open P&L {a['unrealized']:+,.0f}"]
    for p in view.get("positions") or []:
        lines.append(f"{'LONG' if p['side'] == 'long' else 'SHORT'} {p['symbol']} {p['qty']} @ {_px(p['entry'])} -> {_px(p['last'])} ({p['unrealized']:+,.0f})")
    s = view.get("stats") or {}
    if s.get("trades"):
        lines.append(f"Journal: {s['trades']} trades · {s['win_rate']}% won · net {s['net_total']:+,.0f}")
    return "\n".join(lines)


class TelegramBot:
    def __init__(self, token: str, chat_id: str, http: httpx.AsyncClient, app_state) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._chat = str(chat_id)
        self._http = http
        self._state = app_state
        self._offset = 0

    async def _api(self, method: str, payload: dict, timeout: float = 15) -> dict:
        r = await self._http.post(f"{self._base}/{method}", json=payload, timeout=timeout)
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"ok": False}

    async def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        payload = {"chat_id": self._chat, "text": text[:4000] + FOOTER}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
        await self._api("sendMessage", payload)

    async def answer(self, cmd: str, arg: str) -> tuple[str, list[list[tuple[str, str]]] | None]:
        service = self._state.service
        if cmd in ("/start", "/help", ""):
            return HELP, None
        if cmd in ("/call", "/quote"):
            if not arg:
                return f"Usage: {cmd} SYMBOL (e.g. {cmd} RELIANCE or {cmd} NIFTY)", None
            inst = await service.instrument(arg)
            if inst is None:
                return f"{arg}: not found on NSE.", None
            if cmd == "/quote":
                await service.refresh_quotes()
                q = service.cached_quote(inst.instrument_key)
                if not q:
                    return f"{inst.symbol}: no quote right now.", None
                chg = f" ({q.change_pct:+.2f}%)" if q.change_pct is not None else ""
                return f"{inst.symbol}: {_px(q.last_price)}{chg} · {q.freshness}", [[("Call", f"call:{inst.symbol}")]]
            card = await service.intraday_card(inst, with_chart=False)
            return format_call(card), [[("Refresh", f"call:{inst.symbol}"), ("Desk", "desk")]]
        if cmd == "/desk":
            return format_desk(await service.intraday(include_stocks=True)), [[("Refresh", "desk"), ("Market", "market")]]
        if cmd == "/market":
            return format_market(await service.market_context()), [[("Refresh", "market"), ("Desk", "desk")]]
        if cmd == "/paper":
            owners = [d for d, e in getattr(self._state.watchlists, "_devices", {}).items() if e.get("owner")]
            if not owners:
                return "Mark a device as the owner's in the app (Watchlist > notifications) to see its paper account here.", None
            from app.main import _paper_view

            return format_paper(_paper_view(await self._state.paper.get(owners[0]))), [[("Refresh", "paper")]]
        return f"Unknown command {cmd}.\n\n{HELP}", None

    async def _handle(self, text: str) -> None:
        cmd, arg = parse(text)
        try:
            reply, buttons = await self.answer(cmd, arg)
        except ProviderError as exc:
            reply, buttons = f"Data error: {exc.message}", None
        except Exception as exc:
            log.exception("telegram command %s failed", cmd)
            reply, buttons = f"Sorry, {cmd} failed ({exc.__class__.__name__}).", None
        await self.send(reply, buttons)

    async def run(self) -> None:
        while True:
            try:
                res = await self._api("getUpdates", {"offset": self._offset, "timeout": 25,
                                                     "allowed_updates": ["message", "callback_query"]}, timeout=40)
                if not res.get("ok"):
                    await asyncio.sleep(15)  # e.g. another instance polling during a deploy, or a webhook is set
                    continue
                for upd in res.get("result", []):
                    self._offset = upd["update_id"] + 1
                    if "callback_query" in upd:
                        cq = upd["callback_query"]
                        if str(cq.get("message", {}).get("chat", {}).get("id")) != self._chat:
                            continue
                        await self._api("answerCallbackQuery", {"callback_query_id": cq["id"]})
                        data = cq.get("data") or ""
                        kind, _, sym = data.partition(":")
                        await self._handle(f"/{kind} {sym}".strip())
                        continue
                    msg = upd.get("message") or {}
                    if str(msg.get("chat", {}).get("id")) != self._chat:
                        continue  # only the owner's chat
                    if msg.get("text"):
                        await self._handle(msg["text"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("telegram polling: %s", exc.__class__.__name__)
                await asyncio.sleep(10)
