"""FastAPI entry point: REST API + WebSocket quote stream. Run: uvicorn app.main:app --reload"""

import asyncio
import contextlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from datetime import time as dtime
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.ai.nvidia import NvidiaExplainer
from app.alerts import AlertEngine, TelegramNotifier
from app.cache import create_cache
from app.config import get_settings
from app.market_session import IST, is_session_open, is_trading_weekday, now_ist, session_state
from app.models import Timeframe
from app.news import NewsService, NewsStore
from app.providers.base import ProviderError
from app.providers.demo import DemoProvider
from app.providers.upstox import UpstoxAuth, UpstoxProvider
from app.service import MarketService
from app.token_store import TokenStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
settings = get_settings()


class QuoteHub:
    """Single upstream poller fanned out to every connected browser."""

    def __init__(self, service: MarketService) -> None:
        self.service = service
        self.clients: set[WebSocket] = set()
        self._task: asyncio.Task | None = None

    async def join(self, ws: WebSocket) -> None:
        self.clients.add(ws)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        else:
            # The poller may be mid-sleep (60s outside market hours); send a snapshot now.
            try:
                await ws.send_json(self._snapshot(await self.service.refresh_quotes()))
            except ProviderError as exc:
                await ws.send_json({"type": "error", "code": exc.code, "message": exc.message})

    def _snapshot(self, quotes: dict) -> dict:
        return {
            "type": "quotes",
            "server_time": now_ist().isoformat(),
            "session": session_state(),
            "source": self.service.provider.name,
            "quotes": [q.model_dump(mode="json") for q in quotes.values()],
        }

    def leave(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast_alert(self, alert: dict) -> None:
        await self._broadcast({"type": "alert", "alert": alert})

    async def _broadcast(self, message: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.clients.discard(ws)

    async def _run(self) -> None:
        while self.clients:
            delay = settings.quote_poll_seconds if is_session_open() else 60
            try:
                await self._broadcast(self._snapshot(await self.service.refresh_quotes()))
            except ProviderError as exc:
                await self._broadcast({"type": "error", "code": exc.code, "message": exc.message})
                delay = 30 if exc.http_status == 401 else 15
            except Exception:
                log.exception("quote poll failed")
                await self._broadcast({"type": "error", "code": "internal", "message": "Quote update failed"})
                delay = 15
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
    cache = create_cache(settings.redis_url)
    # Redis (when configured) keeps the token across restarts; local files don't survive on free hosting.
    durable = cache if cache.backend == "redis" else None
    tokens = TokenStore(settings.data_dir / ".upstox_token.json", settings.upstox_access_token.get_secret_value(), durable)
    await tokens.restore()
    if settings.data_mode == "upstox":
        if not settings.upstox_api_key or not settings.upstox_api_secret.get_secret_value():
            log.warning("DATA_MODE=upstox but UPSTOX_API_KEY / UPSTOX_API_SECRET are not set")
        provider = UpstoxProvider(settings, http, tokens, cache)
    else:
        log.warning("DATA_MODE=demo: serving SYNTHETIC data, not market data")
        provider = DemoProvider()
    service = MarketService(provider, settings)
    app.state.http, app.state.cache, app.state.tokens = http, cache, tokens
    app.state.service = service
    app.state.hub = QuoteHub(service)
    app.state.auth = UpstoxAuth(settings, http)
    app.state.oauth_states = {}
    app.state.token_request_until = 0.0  # epoch seconds while an approval request is outstanding
    app.state.explainer = NvidiaExplainer(settings, http, cache)
    engine = AlertEngine(service, settings)
    engine.listeners.append(app.state.hub.broadcast_alert)
    if settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id:
        engine.telegram = TelegramNotifier(settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id, http)
    app.state.alerts = engine
    engine.start()
    news = NewsService(settings, http, service, NewsStore(settings.data_dir / "news.db"))
    news.alerts = engine
    app.state.news = news
    news.start()

    async def warm() -> None:
        # Train the combined-outlook models and pattern stats up front so the first page view is fast.
        for tf in ("5m", "15m", "1d"):
            try:
                await service.outlook_model(tf)
                await service.pattern_stats(tf)
                await service.strategy_stats(tf)
            except Exception as exc:  # e.g. Upstox not logged in yet: trained on first request instead
                log.info("warm-up for %s skipped: %s", tf, exc)

    warmer = asyncio.create_task(warm()) if settings.warm_up else None
    keeper = asyncio.create_task(auth_keeper(app, http))
    yield
    if warmer:
        warmer.cancel()
    keeper.cancel()
    await news.stop()
    news.store.close()
    await engine.stop()
    await app.state.hub.stop()
    await cache.close()
    await http.aclose()


async def auth_keeper(app: FastAPI, http: httpx.AsyncClient) -> None:
    """Background upkeep: daily Upstox token request (owner approves on the phone) and a keep-alive
    ping during market hours so free hosting doesn't put the backend to sleep."""
    last_ping = 0.0
    last_request: datetime | None = None
    try:
        hh, mm = (int(x) for x in settings.upstox_auto_request_time.split(":"))
        request_at = dtime(hh, mm)
    except ValueError:
        request_at = dtime(8, 45)
    while True:
        await asyncio.sleep(60)
        now = now_ist()
        trading_hours = is_trading_weekday(now.date()) and dtime(8, 0) <= now.time() <= dtime(16, 0)
        if settings.keepalive_url and trading_hours and time.time() - last_ping >= 600:
            last_ping = time.time()
            with contextlib.suppress(httpx.HTTPError):
                await http.get(settings.keepalive_url, timeout=20)
        if (settings.upstox_auto_request and settings.data_mode == "upstox" and trading_hours
                and request_at <= now.time() <= dtime(15, 30)
                and app.state.tokens.get() is None
                and (last_request is None or now - last_request >= timedelta(hours=3))):
            last_request = now
            try:
                await start_token_request(app)
                log.info("Upstox token approval requested automatically; approve it in the Upstox app")
            except ProviderError as exc:
                log.warning("automatic Upstox token request failed: %s", exc.message)


async def start_token_request(app: FastAPI) -> dict:
    data = await app.state.auth.request_token()
    expiry_ms = data.get("authorization_expiry")
    until = int(expiry_ms) / 1000 if expiry_ms else time.time() + 6 * 3600
    app.state.token_request_until = until
    return {"authorization_expiry": datetime.fromtimestamp(until, IST).isoformat(), "notifier_url": data.get("notifier_url")}


app = FastAPI(title="AI Stock Intelligence API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, exc: ProviderError):
    body = {"error": exc.code, "message": exc.message}
    if exc.http_status == 401:
        body["owner_action"] = "reconnect_upstox"
    return JSONResponse(status_code=exc.http_status, content=body)


def svc(request: Request) -> MarketService:
    return request.app.state.service


# ---- health & auth ------------------------------------------------------
@app.get("/api/health")
async def health(request: Request):
    tokens: TokenStore = request.app.state.tokens
    return {
        "status": "ok",
        "data_mode": settings.data_mode,
        "provider": svc(request).provider.name,
        "upstox": ({**tokens.status(), "owner_protected": bool(settings.admin_key.get_secret_value()),
                    "auto_request": settings.upstox_auto_request,
                    "approval_pending": request.app.state.token_request_until > time.time() and tokens.get() is None}
                   if settings.data_mode == "upstox" else None),
        "upstox_configured": bool(settings.upstox_api_key and settings.upstox_api_secret.get_secret_value()),
        "cache": request.app.state.cache.backend,
        "nvidia_configured": bool(settings.nvidia_api_key.get_secret_value()),
        "nvidia_model": settings.nvidia_model,
        "news": {"enabled": settings.news_enabled, "source": "Google News (RSS)",
                 "scored_by": "nvidia" if settings.nvidia_api_key.get_secret_value() else "keywords"},
        "session": session_state(),
        "server_time": now_ist().isoformat(),
        "quote_poll_seconds": settings.quote_poll_seconds,
        "alerts": {
            "enabled": settings.alerts_enabled,
            "telegram": bool(settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id),
            "move_levels": request.app.state.alerts.levels,
            "fast_move_pct": settings.alert_fast_move_pct,
            "fast_window_minutes": settings.alert_fast_window_minutes,
            "signal_timeframe": settings.alert_signal_timeframe,
        },
    }


def require_owner(key: str) -> None:
    """Connecting Upstox is for the site owner only: a visitor's login would replace the token."""
    if settings.data_mode != "upstox":
        raise HTTPException(400, "Set DATA_MODE=upstox to connect Upstox")
    if not settings.upstox_api_key:
        raise HTTPException(500, "UPSTOX_API_KEY is not configured on the backend")
    expected = settings.admin_key.get_secret_value()
    if expected and not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(403, "Owner key required to connect Upstox")


def owner_allowed(user_id: str | None) -> bool:
    owner = settings.upstox_owner_user_id.strip().upper()
    return not owner or (user_id or "").strip().upper() == owner


@app.get("/api/auth/upstox/login")
async def upstox_login(request: Request, key: str = ""):
    require_owner(key)
    states: dict[str, float] = request.app.state.oauth_states
    now = time.time()
    for s, exp in list(states.items()):
        if exp < now:
            states.pop(s)
    state = secrets.token_urlsafe(24)
    states[state] = now + 600
    return RedirectResponse(request.app.state.auth.login_url(state))


@app.get("/api/auth/upstox/callback")
async def upstox_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    states: dict[str, float] = request.app.state.oauth_states
    expiry = states.pop(state, None)
    if not expiry or expiry < time.time():
        raise HTTPException(400, "Invalid or expired OAuth state; start again from /api/auth/upstox/login")
    if error or not code:
        return RedirectResponse(f"{settings.frontend_origin}/?upstox=denied")
    try:
        body = await request.app.state.auth.exchange_code(code)
    except ProviderError as exc:
        log.warning("Upstox token exchange failed: %s", exc.message)
        return RedirectResponse(f"{settings.frontend_origin}/?{urlencode({'upstox': 'error', 'message': exc.message})}")
    if not owner_allowed(body.get("user_id")):
        log.warning("refused Upstox login from a non-owner account")
        msg = "This Upstox account is not the site owner's; the existing connection was kept."
        return RedirectResponse(f"{settings.frontend_origin}/?{urlencode({'upstox': 'error', 'message': msg})}")
    await request.app.state.tokens.set(body["access_token"], user_id=body.get("user_id"), user_name=body.get("user_name"))
    log.info("Upstox access token stored (expires %s)", request.app.state.tokens.status()["expires_at"])
    return RedirectResponse(f"{settings.frontend_origin}/?upstox=connected")


@app.post("/api/auth/upstox/request")
async def upstox_token_request(request: Request, key: str = Query("")):
    """Ask Upstox to send the owner an approval request (Upstox app / web). No browser login or OTP."""
    require_owner(key)
    try:
        return {"status": "requested", **await start_token_request(request.app),
                "message": "Approve the request in the Upstox app (or the Upstox developer apps page). "
                           "The token arrives automatically."}
    except ProviderError as exc:
        raise HTTPException(502, f"Upstox refused the token request: {exc.message}") from exc


@app.post("/api/auth/upstox/notifier")
async def upstox_notifier(request: Request):
    """Webhook: Upstox POSTs the approved access token here (set as the Notifier URL on the Upstox app)."""
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected JSON") from None
    token = payload.get("access_token")
    if payload.get("message_type", "access_token") != "access_token" or not token:
        return {"status": "ignored"}
    if payload.get("client_id") != settings.upstox_api_key:
        raise HTTPException(403, "Unknown client")
    if request.app.state.token_request_until < time.time():
        raise HTTPException(409, "No token request is pending")
    try:
        profile = await request.app.state.auth.profile(token)  # proves the token is real and whose it is
    except ProviderError:
        raise HTTPException(401, "Token rejected by Upstox") from None
    user_id = profile.get("user_id") or payload.get("user_id")
    if not owner_allowed(user_id):
        raise HTTPException(403, "Token is not from the owner's Upstox account")
    expires_ms = payload.get("expires_at")
    expires_at = datetime.fromtimestamp(int(expires_ms) / 1000, IST) if expires_ms else None
    await request.app.state.tokens.set(token, expires_at=expires_at, user_id=user_id,
                                       user_name=profile.get("user_name"), source="approval")
    request.app.state.token_request_until = 0.0
    log.info("Upstox access token received via approval (expires %s)", request.app.state.tokens.status()["expires_at"])
    return {"status": "stored"}


# ---- market data --------------------------------------------------------
@app.get("/api/instruments")
async def instruments(request: Request):
    equities, missing = await svc(request).universe()
    idx, idx_missing = await svc(request).indices()
    return {
        "source": svc(request).provider.name,
        "equities": [i.model_dump() for i in equities],
        "indices": [i.model_dump() for i in idx],
        "unresolved_symbols": missing,
        "unavailable_indices": idx_missing,
    }


@app.get("/api/quotes")
async def quotes(request: Request):
    service = svc(request)
    q = await service.refresh_quotes()
    idx, idx_missing = await service.indices()
    idx_keys = {i.instrument_key for i in idx}
    return {
        "source": service.provider.name,
        "session": session_state(),
        "server_time": now_ist().isoformat(),
        "quotes": [v.model_dump(mode="json") for k, v in q.items() if k not in idx_keys],
        "indices": [q[i.instrument_key].model_dump(mode="json") if i.instrument_key in q else
                    {"symbol": i.symbol, "freshness": "unavailable"} for i in idx]
                   + [{"symbol": m, "freshness": "unavailable"} for m in idx_missing],
    }


@app.get("/api/scanner")
async def scanner(request: Request, timeframe: Timeframe = Query("1d")):
    return await svc(request).scan(timeframe)


@app.get("/api/patterns/stats")
async def pattern_stats(request: Request, timeframe: Timeframe = Query("1d")):
    """Per-pattern success rates measured across the whole universe (cached 30 minutes)."""
    return await svc(request).pattern_stats(timeframe)


@app.get("/api/intraday")
async def intraday(request: Request, stocks: bool = Query(True)):
    """Intraday desk: NIFTY 50 / BANK NIFTY / SENSEX 5-minute BUY/SELL (confirmed by 15-minute), then stocks."""
    return await svc(request).intraday(include_stocks=stocks)


@app.get("/api/strategies/stats")
async def strategy_stats(request: Request, timeframe: Timeframe = Query("1d")):
    """Each strategy's and candlestick's hit rate vs. random, pooled across the universe (cached 30 minutes)."""
    return await svc(request).strategy_stats(timeframe)


@app.get("/api/news")
async def news_feed(request: Request, limit: int = Query(30, ge=1, le=100)):
    """Latest headlines across the universe, newest first."""
    return request.app.state.news.feed(limit)


@app.get("/api/news/{symbol}")
async def stock_news(request: Request, symbol: str):
    """Live headlines for one stock, their sentiment, and the news-adjusted up/down probability."""
    inst = await svc(request).instrument(symbol)
    if inst is None:
        raise HTTPException(404, f"{symbol.upper()} is not in the configured universe")
    return await request.app.state.news.snapshot(inst)


@app.get("/api/alerts")
async def alerts(request: Request, limit: int = Query(50, ge=1, le=200)):
    return {"alerts": request.app.state.alerts.recent(limit), "session": session_state()}


@app.post("/api/alerts/test")
async def test_alert(request: Request):
    return await request.app.state.alerts.send_test()


@app.get("/api/movers")
async def movers(request: Request):
    """Today's gainers/losers from live quotes, plus moves over the last alert window."""
    service = svc(request)
    equities, _ = await service.universe()
    quotes = await service.refresh_quotes()
    rows = [
        {"symbol": i.symbol, "name": i.name, "price": q.last_price, "change_pct": q.change_pct, "freshness": q.freshness}
        for i in equities
        if (q := quotes.get(i.instrument_key)) and q.change_pct is not None
    ]
    rows.sort(key=lambda r: -r["change_pct"])
    return {
        "session": session_state(),
        "gainers": [r for r in rows if r["change_pct"] > 0][:5],
        "losers": [r for r in reversed(rows) if r["change_pct"] < 0][:5],
        "fast": request.app.state.alerts.fast_movers()[:5],
        "fast_window_minutes": settings.alert_fast_window_minutes,
    }


@app.get("/api/today")
async def today(request: Request):
    result = await svc(request).today()
    result["session"] = session_state()
    return result


async def _stock_detail(request: Request, symbol: str, timeframe: Timeframe) -> dict:
    service = svc(request)
    inst = await service.instrument(symbol)
    if inst is None:
        raise HTTPException(404, f"{symbol.upper()} is not in the configured universe")
    with contextlib.suppress(ProviderError):
        await service.refresh_quotes()
    q = service.cached_quote(inst.instrument_key)
    analysis = await service.analyse(inst, timeframe, with_series=True)
    outlook = analysis.pop("outlook", None) or {"status": "not_available", "message": "No candle data."}
    return {
        "instrument": inst.model_dump(),
        "timeframe": timeframe,
        "source": service.provider.name,
        "quote": q.model_dump(mode="json") if q else None,
        "generated_at": now_ist().isoformat(),
        "model_outlook": outlook,
        **analysis,
    }


@app.get("/api/stocks/{symbol}")
async def stock_detail(request: Request, symbol: str, timeframe: Timeframe = Query("1d")):
    return await _stock_detail(request, symbol, timeframe)


@app.post("/api/stocks/{symbol}/explain")
async def explain_stock(request: Request, symbol: str, timeframe: Timeframe = Query("1d")):
    """NVIDIA NIM rephrases the computed signal; it adds no data of its own."""
    explainer: NvidiaExplainer = request.app.state.explainer
    if not explainer.configured:
        raise ProviderError("nvidia_not_configured", "NVIDIA_API_KEY is not set on the backend.", 503)
    detail = await _stock_detail(request, symbol, timeframe)
    return await explainer.explain(detail)


@app.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin != settings.frontend_origin:
        await ws.close(code=1008)
        return
    await ws.accept()
    hub: QuoteHub = ws.app.state.hub
    await hub.join(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; client messages are ignored
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(ws)
