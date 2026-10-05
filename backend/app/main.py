"""FastAPI entry point: REST API + WebSocket quote stream. Run: uvicorn app.main:app --reload"""

import asyncio
import contextlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.ai.nvidia import NvidiaExplainer
from app.alerts import AlertEngine, TelegramNotifier
from app.cache import create_cache
from app.config import get_settings
from app.market_session import is_session_open, now_ist, session_state
from app.models import Timeframe
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
    tokens = TokenStore(settings.data_dir / ".upstox_token.json", settings.upstox_access_token.get_secret_value())
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
    app.state.explainer = NvidiaExplainer(settings, http, cache)
    engine = AlertEngine(service, settings)
    engine.listeners.append(app.state.hub.broadcast_alert)
    if settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id:
        engine.telegram = TelegramNotifier(settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id, http)
    app.state.alerts = engine
    engine.start()
    yield
    await engine.stop()
    await app.state.hub.stop()
    await cache.close()
    await http.aclose()


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
        body["login_url"] = "/api/auth/upstox/login"
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
        "upstox": tokens.status() if settings.data_mode == "upstox" else None,
        "upstox_configured": bool(settings.upstox_api_key and settings.upstox_api_secret.get_secret_value()),
        "cache": request.app.state.cache.backend,
        "nvidia_configured": bool(settings.nvidia_api_key.get_secret_value()),
        "nvidia_model": settings.nvidia_model,
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


@app.get("/api/auth/upstox/login")
async def upstox_login(request: Request):
    if settings.data_mode != "upstox":
        raise HTTPException(400, "Set DATA_MODE=upstox to connect Upstox")
    if not settings.upstox_api_key:
        raise HTTPException(500, "UPSTOX_API_KEY is not configured on the backend")
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
        token = await request.app.state.auth.exchange_code(code)
    except ProviderError as exc:
        log.warning("Upstox token exchange failed: %s", exc.message)
        return RedirectResponse(f"{settings.frontend_origin}/?{urlencode({'upstox': 'error', 'message': exc.message})}")
    request.app.state.tokens.set(token)
    log.info("Upstox access token stored (expires %s)", request.app.state.tokens.status()["expires_at"])
    return RedirectResponse(f"{settings.frontend_origin}/?upstox=connected")


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
    return {
        "instrument": inst.model_dump(),
        "timeframe": timeframe,
        "source": service.provider.name,
        "quote": q.model_dump(mode="json") if q else None,
        "generated_at": now_ist().isoformat(),
        "model_outlook": {
            "status": "not_available",
            "message": "Horizon-specific ML probabilities are not built yet; they will be shown only after out-of-sample validation.",
        },
        "news": {"status": "not_configured", "message": "No licensed news source is configured."},
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
