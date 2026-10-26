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
from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app import paper
from app.ai.debate import Debate
from app.ai.nvidia import NvidiaExplainer
from app.alerts import AlertEngine, TelegramNotifier
from app.auth_users import COOKIE, SESSION_DAYS, WS_TOKEN_SECONDS, Auth
from app.cache import MemoryCache, create_cache
from app.config import get_settings
from app.forex.api import router as forex_router
from app.forex.news import ForexNews
from app.forex.service import ForexService
from app.market_session import CURRENCY, IST, holiday_name, is_session_open, is_trading_weekday, now_ist, session_state, set_calendar
from app.models import Timeframe
from app.news import NewsService, NewsStore
from app.providers.base import ProviderError, parse_holidays
from app.providers.demo import DemoProvider
from app.providers.upstox import UpstoxAuth, UpstoxProvider
from app.paper import PaperStore
from app.service import MarketService
from app.telegram_bot import TelegramBot
from app.push import PushSender
from app.token_store import TokenStore
from app.watchlists import WatchlistStore, clean_symbols, valid_device

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
settings = get_settings()
site_auth = Auth(settings.app_users, settings.session_secret.get_secret_value())


class QuoteHub:
    """Single upstream poller fanned out to every connected browser."""

    def __init__(self, service: MarketService) -> None:
        self.service = service
        self.clients: set[WebSocket] = set()
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

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
            # Sleep, but wake early when poked (e.g. right after Upstox reconnects).
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)
            self._wake.clear()

    def poke(self) -> None:
        """Poll now instead of after the current sleep."""
        self._wake.set()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
    # Short-lived caches (candles, AI answers) live in this process's memory: faster than a network round trip
    # and they were nearly all of the Redis traffic. Redis (when configured) only keeps what must survive a
    # restart on free hosting: the Upstox token, watchlists, push keys, paper accounts, IV and debate history.
    cache = MemoryCache(max_items=6000, max_bytes=settings.memory_cache_mb * 1_000_000)
    durable = create_cache(settings.redis_url) if settings.redis_url else None
    store = durable or cache
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
    service.cache = store
    # Forex: its own service (pairs, session, models, caches) and news, on the same data provider.
    forex = ForexService(provider, settings)
    app.state.forex = forex
    app.state.http, app.state.cache, app.state.durable, app.state.tokens = http, cache, durable, tokens
    app.state.service = service
    app.state.hub = QuoteHub(service)
    app.state.auth = UpstoxAuth(settings, http)
    app.state.oauth_states = {}
    app.state.token_request_until = 0.0  # epoch seconds while an approval request is outstanding
    app.state.explainer = NvidiaExplainer(settings, http, cache)
    app.state.debate = Debate(app.state.explainer, store)
    engine = AlertEngine(service, settings)
    engine.listeners.append(app.state.hub.broadcast_alert)
    if settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id:
        engine.telegram = TelegramNotifier(settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id, http)
    watchlists = WatchlistStore(settings.data_dir / "watchlists.json", durable)
    await watchlists.load()
    push = PushSender(settings.push_contact, settings.data_dir / "vapid.json", durable,
                      settings.vapid_public_key, settings.vapid_private_key.get_secret_value())
    await push.load()
    app.state.watchlists, app.state.push = watchlists, push
    paper_store = PaperStore(settings.data_dir / "paper", durable)
    await paper_store.load_all()
    app.state.paper = paper_store
    paper_task = asyncio.create_task(paper_runner(app))
    bot_task = None
    if settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id:
        bot = TelegramBot(settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id, http, app.state)
        bot_task = asyncio.create_task(bot.run())
    engine.watchlists, engine.push = watchlists, push
    app.state.alerts = engine
    engine.start()
    news = NewsService(settings, http, service, NewsStore(settings.data_dir / "news.db"))
    news.alerts = engine
    app.state.news = news
    news.start()
    forex_news = ForexNews(settings, http, forex, NewsStore(settings.data_dir / "news_forex.db"))
    app.state.forex_news = forex_news
    forex_news.start()

    async def warm() -> None:
        # Train the combined-outlook models and pattern stats up front so the first page view is fast.
        for tf in ("5m", "15m", "1d"):
            try:
                await service.outlook_model(tf)
                await service.pattern_stats(tf)
                await service.strategy_stats(tf)
            except Exception as exc:  # e.g. Upstox not logged in yet: trained on first request instead
                log.info("warm-up for %s skipped: %s", tf, exc)
        await forex.warm()  # after the stocks, so the two don't compete for the CPU at start-up

    warmer = asyncio.create_task(warm()) if settings.warm_up else None
    desk_warmer = asyncio.create_task(service.desk_warmer()) if settings.warm_up else None
    forex_desk_warmer = asyncio.create_task(forex.desk_warmer()) if settings.warm_up else None
    options_sampler = asyncio.create_task(service.options_sampler()) if settings.warm_up else None
    keeper = asyncio.create_task(auth_keeper(app, http))
    calendar = asyncio.create_task(calendar_keeper(app))
    yield
    if warmer:
        warmer.cancel()
    for task in (desk_warmer, forex_desk_warmer, options_sampler):
        if task:
            task.cancel()
    keeper.cancel()
    calendar.cancel()
    paper_task.cancel()
    if bot_task:
        bot_task.cancel()
    await news.stop()
    news.store.close()
    await forex_news.stop()
    forex_news.store.close()
    await engine.stop()
    await app.state.hub.stop()
    await cache.close()
    if durable is not None:
        await durable.close()
    await http.aclose()


PAPER_TICK_SECONDS = 3


async def paper_runner(app: FastAPI) -> None:
    """Fill paper orders and run the intraday square-off from the server's own quotes, every few seconds
    while the market is open."""
    store: PaperStore = app.state.paper
    service: MarketService = app.state.service
    last_save = 0.0
    while True:
        try:
            now = now_ist()
            active = store.active()
            if active and is_session_open(now):
                for sym in {x["symbol"] for _, acc in active for x in acc["orders"] + acc["positions"]}:
                    await service.instrument(sym)  # makes sure its quote is polled (also after a restart)
                quotes = await service.refresh_quotes()
                ltp = {k: q.last_price for k, q in quotes.items() if q.last_price}
                for device, acc in active:
                    events = paper.process(acc, ltp, now, True)
                    paper.snapshot_equity(acc, now)
                    if events or time.monotonic() - last_save > 300:
                        await store.save(device)
                if time.monotonic() - last_save > 300:
                    last_save = time.monotonic()
                await asyncio.sleep(PAPER_TICK_SECONDS)
            else:
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("paper runner: %s", exc)
            await asyncio.sleep(10)


async def calendar_keeper(app: FastAPI) -> None:
    """Load this year's NSE / currency holidays from Upstox, then refresh them every 12 hours
    (retrying every 10 minutes until Upstox is connected)."""
    while True:
        delay = 12 * 3600
        try:
            rows = await app.state.service.provider.market_holidays()
            for exchange, (closed, special) in parse_holidays(rows).items():
                set_calendar(exchange, closed, special)
            if rows:
                log.info("market holiday calendar loaded: %d entries", len(rows))
        except Exception as exc:  # e.g. not connected yet
            log.info("market holiday calendar not loaded yet: %s", exc)
            delay = 600
        await asyncio.sleep(delay)


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
app.include_router(forex_router)
# Chart payloads are large JSON arrays: compressing them is the single biggest speed-up on mobile.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Reached without a site login: health/keep-alive, the login itself, and what Upstox calls (the Upstox owner
# endpoints are protected by the admin key instead).
OPEN_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/me",
              "/api/auth/upstox/login", "/api/auth/upstox/callback", "/api/auth/upstox/request", "/api/auth/upstox/notifier"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if (site_auth.enabled and path.startswith("/api") and path not in OPEN_PATHS and request.method != "OPTIONS"
            and site_auth.verify(request.cookies.get(COOKIE)) is None):
        return JSONResponse(status_code=401, content={"error": "login_required", "message": "Please log in."})
    return await call_next(request)


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
        "login_required": site_auth.enabled,
        "data_mode": settings.data_mode,
        "provider": svc(request).provider.name,
        "upstox": ({**tokens.status(), "owner_protected": bool(settings.admin_key.get_secret_value()),
                    "auto_request": settings.upstox_auto_request,
                    "approval_pending": request.app.state.token_request_until > time.time() and tokens.get() is None}
                   if settings.data_mode == "upstox" else None),
        "upstox_configured": bool(settings.upstox_api_key and settings.upstox_api_secret.get_secret_value()),
        "cache": {"backend": "memory", **request.app.state.cache.status()},
        "durable_store": (request.app.state.durable.status() if request.app.state.durable is not None else None),
        "nvidia_configured": bool(settings.nvidia_api_key.get_secret_value()),
        "nvidia_model": settings.nvidia_model,
        "news": {"enabled": settings.news_enabled, "source": "Google News (RSS)",
                 "scored_by": "nvidia" if settings.nvidia_api_key.get_secret_value() else "keywords"},
        "session": session_state(),
        "holiday": holiday_name(now_ist().date()),
        "currency_session": session_state(hours=CURRENCY),
        "currency_holiday": holiday_name(now_ist().date(), CURRENCY),
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


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


@app.post("/api/auth/login")
async def login(request: Request, body: dict = Body(...)):
    if not site_auth.enabled:  # open site: a placeholder cookie so the frontend's page guard lets everyone in
        res = JSONResponse({"user": None, "login_required": False})
        res.set_cookie(COOKIE, "open", max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", path="/")
        return res
    username, password = str(body.get("username") or "")[:64], str(body.get("password") or "")[:200]
    ip = client_ip(request)
    if site_auth.locked(username, ip):
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    user = site_auth.check(username, password, ip)
    if user is None:
        await asyncio.sleep(0.5)
        raise HTTPException(401, "Wrong username or password.")
    log.info("site login: %s", user)
    res = JSONResponse({"user": user, "login_required": True})
    res.set_cookie(COOKIE, site_auth.issue(user), max_age=SESSION_DAYS * 86400, httponly=True,
                   secure=settings.session_cookie_secure, samesite="lax", path="/")
    return res


@app.post("/api/auth/logout")
async def logout():
    res = JSONResponse({"user": None})
    res.delete_cookie(COOKIE, path="/", httponly=True, secure=settings.session_cookie_secure, samesite="lax")
    return res


@app.get("/api/auth/me")
async def me(request: Request):
    if not site_auth.enabled:
        return {"user": None, "login_required": False}
    user = site_auth.verify(request.cookies.get(COOKIE))
    if user is None:
        return JSONResponse(status_code=401, content={"error": "login_required", "message": "Please log in."})
    return {"user": user, "login_required": True}


@app.get("/api/auth/ws-token")
async def ws_token(request: Request):
    """Short-lived token for the quotes websocket (it connects to the backend directly, without the cookie)."""
    user = site_auth.verify(request.cookies.get(COOKIE))
    return {"token": site_auth.issue(user, kind="ws", seconds=WS_TOKEN_SECONDS) if user else None}


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
    request.app.state.hub.poke()
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
    request.app.state.hub.poke()
    request.app.state.token_request_until = 0.0
    log.info("Upstox access token received via approval (expires %s)", request.app.state.tokens.status()["expires_at"])
    return {"status": "stored"}


# ---- watchlists & notifications --------------------------------------------
def watch_store(request: Request, device: str) -> WatchlistStore:
    if not valid_device(device):
        raise HTTPException(400, "Invalid device id")
    return request.app.state.watchlists


@app.get("/api/instruments/search")
async def search_instruments(request: Request, q: str = Query("", max_length=40)):
    """Indices and NSE stocks matching a symbol or company name (for adding to a watchlist)."""
    return {"results": await svc(request).search(q)}


@app.get("/api/watchlist/{device}")
async def get_watchlist(request: Request, device: str):
    return watch_store(request, device).get(device)


@app.put("/api/watchlist/{device}")
async def put_watchlist(request: Request, device: str, symbols: list[str] = Body(..., embed=True)):
    """Replace this device's watchlist. Unknown symbols are dropped."""
    store = watch_store(request, device)
    resolved = []
    for sym in symbols[:40]:
        inst = await svc(request).instrument(sym)
        if inst is not None:
            resolved.append(inst.symbol)
    return {"symbols": await store.set_symbols(device, resolved)}


@app.get("/api/watchlist/{device}/calls")
async def watchlist_calls(request: Request, device: str, s: str = Query("", max_length=800)):
    """Intraday BUY / SELL / exit call for every stock on this device's watchlist (or the list in `s`,
    which the page sends so a change shows up before it has synced)."""
    store = watch_store(request, device)
    symbols = clean_symbols(s.split(",")) if s else store.get(device)["symbols"]
    return await svc(request).watchlist_calls(symbols)


@app.get("/api/push/key")
async def push_key(request: Request):
    return {"public_key": request.app.state.push.public_key}


@app.put("/api/watchlist/{device}/push")
async def put_push(request: Request, device: str, subscription: dict = Body(..., embed=True)):
    """Turn on phone/desktop notifications for this device's watchlist."""
    if not isinstance(subscription.get("endpoint"), str) or not subscription["endpoint"].startswith("https://"):
        raise HTTPException(400, "Invalid push subscription")
    await watch_store(request, device).set_push(device, subscription)
    return {"push": True}


@app.delete("/api/watchlist/{device}/push")
async def delete_push(request: Request, device: str):
    await watch_store(request, device).set_push(device, None)
    return {"push": False}


@app.put("/api/watchlist/{device}/owner")
async def put_owner(request: Request, device: str, key: str = Query(""), owner: bool = Query(True)):
    """Mark this device as the owner's: its watchlist decides what is sent to Telegram."""
    expected = settings.admin_key.get_secret_value()
    if expected and not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(403, "Owner key required")
    await watch_store(request, device).set_owner(device, owner)
    return {"owner": owner}


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


@app.get("/api/options/{symbol}")
async def option_chain(request: Request, symbol: str, expiry: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Option chain for NIFTY 50 / BANK NIFTY / SENSEX, OI summary and which option to buy for the intraday call."""
    return await svc(request).option_chain(symbol, expiry)


@app.get("/api/timemachine/{symbol}")
async def time_machine(request: Request, symbol: str, side: str = Query(..., pattern="^(BUY|SELL)$"), entry: float = Query(..., gt=0),
                       stop: float = Query(..., gt=0), target: float = Query(..., gt=0),
                       until: str | None = Query(None, pattern=r"^\d{2}:\d{2}$"), seed: int = Query(19, ge=0, le=10**9),
                       replays: int = Query(10_000, ge=1_000, le=20_000)):
    """Optional Day-trader tool: odds of target first / stop first / neither for one plan (10,000 seeded replays)."""
    inst = await svc(request).instrument(symbol)
    if inst is None:
        raise HTTPException(404, f"{symbol.upper()} is not in the configured universe")
    return await svc(request).time_machine(inst, side, entry, stop, target, until, seed, replays)


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


def _paper_view(acc: dict) -> dict:
    positions = []
    for p in acc["positions"]:
        long = p["side"] == "long"
        unreal = ((p["last"] - p["entry"]) if long else (p["entry"] - p["last"])) * p["qty"]
        legs = {o["role"]: {"id": o["id"], "trigger": o.get("trigger"), "price": o.get("price"), "trail_pct": o.get("trail_pct")}
                for o in acc["orders"] if o.get("position") == p["id"] and o["status"] == "open"}
        positions.append({**{k: v for k, v in p.items() if k not in ("best", "worst")}, "unrealized": round(unreal, 2),
                          "unrealized_pct": round(100 * unreal / (p["entry"] * p["qty"]), 2), "legs": legs})
    orders = [o for o in acc["orders"] if o["role"] == "entry"]
    return {
        "account": {"capital": acc["capital"], **paper.mark(acc)},
        "positions": positions,
        "orders": sorted(orders, key=lambda o: (o["status"] != "open", o["placed"]), reverse=False)[:60],
        "journal": list(reversed(acc["journal"]))[:200],
        "stats": paper.journal_stats(acc["journal"]),
        "equity": acc["equity"],
        "session": session_state(),
        "rules": "Paper money only: nothing is sent to a broker. Fills use the server's latest Upstox quote while the market "
                 "is open (market and stop orders pay estimated slippage; limits never fill worse than the limit). Intraday "
                 "positions are squared off at 15:15. Charges at Upstox rates. No leverage.",
    }


async def _paper_account(request: Request, device: str) -> dict:
    try:
        return await request.app.state.paper.get(device)
    except paper.PaperError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/paper/{device}")
async def paper_get(request: Request, device: str):
    return _paper_view(await _paper_account(request, device))


@app.post("/api/paper/{device}/orders")
async def paper_order(request: Request, device: str, body: dict = Body(...)):
    """Place a paper order. The client never sets the fill price: the server fills from its own quotes."""
    acc = await _paper_account(request, device)
    service = svc(request)
    inst = await service.instrument(str(body.get("symbol") or ""))
    if inst is None:
        raise HTTPException(404, "Symbol not found on NSE.")
    with contextlib.suppress(ProviderError):
        await service.refresh_quotes()
    q = service.cached_quote(inst.instrument_key)
    try:
        order = paper.validate_order(acc, {**body, "ref_price": q.last_price if q else None})
    except paper.PaperError as exc:
        raise HTTPException(400, str(exc)) from None
    now = now_ist()
    placed = paper.place(acc, order, inst.symbol, inst.instrument_key, now)
    if q and q.last_price and is_session_open(now):
        paper.process(acc, {inst.instrument_key: q.last_price}, now, True)
    await request.app.state.paper.save(device)
    return {"order": placed, **_paper_view(acc)}


@app.delete("/api/paper/{device}/orders/{order_id}")
async def paper_cancel(request: Request, device: str, order_id: str):
    acc = await _paper_account(request, device)
    try:
        paper.cancel(acc, order_id)
    except paper.PaperError as exc:
        raise HTTPException(400, str(exc)) from None
    await request.app.state.paper.save(device)
    return _paper_view(acc)


@app.post("/api/paper/{device}/positions/{pos_id}/close")
async def paper_close(request: Request, device: str, pos_id: str, body: dict = Body(default={})):
    """Close a position at the server's current quote (plus slippage), while the market is open."""
    acc = await _paper_account(request, device)
    pos = next((p for p in acc["positions"] if p["id"] == pos_id), None)
    if pos is None:
        raise HTTPException(404, "No such open position.")
    service = svc(request)
    with contextlib.suppress(ProviderError):
        await service.refresh_quotes()
    q = service.cached_quote(pos["instrument_key"])
    if not is_session_open(now_ist()) or not q or not q.last_price:
        raise HTTPException(409, "The market is closed (or there is no live quote): close it during market hours.")
    slip = paper.SLIPPAGE_PER_SIDE["intraday" if pos["product"] == "intraday" else "delivery"]
    price = q.last_price * (1 - slip) if pos["side"] == "long" else q.last_price * (1 + slip)
    paper.close_position(acc, pos_id, price, now_ist(), "manual", str(body.get("note") or ""))
    await request.app.state.paper.save(device)
    return _paper_view(acc)


@app.patch("/api/paper/{device}/journal/{trade_id}")
async def paper_journal_note(request: Request, device: str, trade_id: str, body: dict = Body(...)):
    acc = await _paper_account(request, device)
    t = next((x for x in acc["journal"] if x["id"] == trade_id), None)
    if t is None:
        raise HTTPException(404, "No such trade.")
    for k in ("exit_note", "lesson"):
        if k in body:
            t[k] = " ".join(str(body[k] or "").split())[:500]
    await request.app.state.paper.save(device)
    return t


@app.post("/api/paper/{device}/reset")
async def paper_reset(request: Request, device: str, body: dict = Body(default={})):
    await _paper_account(request, device)
    try:
        capital = float(body.get("capital") or paper.DEFAULT_CAPITAL)
    except (TypeError, ValueError):
        capital = paper.DEFAULT_CAPITAL
    if not 10_000 <= capital <= 100_000_000:
        raise HTTPException(400, "Starting capital must be between ₹10,000 and ₹10 crore.")
    request.app.state.paper.accounts[device] = paper.new_account(capital)
    await request.app.state.paper.save(device)
    return _paper_view(request.app.state.paper.accounts[device])


@app.get("/api/strategies/lab")
async def strategy_lab(request: Request, timeframe: Timeframe = Query("1d")):
    """Strategy leaderboard: every strategy pooled over the universe, after costs, ranked by evidence then Sharpe."""
    return await svc(request).strategy_lab(timeframe)


@app.get("/api/strategies/lab/{key}")
async def strategy_tearsheet(request: Request, key: str, timeframe: Timeframe = Query("1d")):
    """One strategy's tearsheet: equity, drawdown, monthly returns, rolling Sharpe, trade distribution."""
    return await svc(request).strategy_tearsheet(timeframe, key)


@app.get("/api/market/context")
async def market_context(request: Request):
    """Market board: India VIX, breadth, sector rotation, risk-on/defensive vote, expiries, F&O ban list."""
    usdinr = None
    with contextlib.suppress(Exception):  # the rupee vote is optional
        usdinr = await request.app.state.forex.instrument("USDINR")
    return await svc(request).market_context(usdinr)


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


@app.post("/api/stocks/{symbol}/debate")
async def debate_stock(request: Request, symbol: str, timeframe: Timeframe = Query("1d")):
    """AI debate (bull, bear, judge, risk) on NVIDIA NIM over the app's own computed facts, streamed as events."""
    import json

    debate: Debate = request.app.state.debate
    if not debate.configured:
        raise HTTPException(503, "NVIDIA_API_KEY is not set on the backend.")
    detail = await _stock_detail(request, symbol, timeframe)
    context = None
    with contextlib.suppress(Exception):
        context = await asyncio.wait_for(svc(request).market_context(), 20)

    async def events():
        try:
            async for ev in debate.run(detail, context):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except ProviderError as exc:
            yield f"data: {json.dumps({'step': 'error', 'message': exc.message})}\n\n"
        except Exception as exc:  # keep the stream well-formed
            log.exception("debate failed for %s", symbol)
            yield f"data: {json.dumps({'step': 'error', 'message': f'Debate failed: {exc.__class__.__name__}'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
    if site_auth.enabled and site_auth.verify(ws.query_params.get("token"), kind="ws") is None:
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
