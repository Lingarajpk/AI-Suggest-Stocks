"""Forex REST API under /api/forex: separate from the stock routes, backed by the ForexService."""

from fastapi import APIRouter, HTTPException, Query, Request

from app.forex import PAIRS
from app.forex.news import ForexNews
from app.forex.service import ForexService
from app.models import Timeframe

router = APIRouter(prefix="/api/forex", tags=["forex"])


def fx(request: Request) -> ForexService:
    return request.app.state.forex


@router.get("/quotes")
async def forex_quotes(request: Request):
    """Live prices of the four pairs' front-month futures (the forex pages poll this every few seconds)."""
    return await fx(request).quotes_payload()


@router.get("/desk")
async def forex_desk(request: Request):
    """Forex desk: 5-minute BUY/SELL for USDINR, EURINR, GBPINR and JPYINR, confirmed by the 15-minute trend."""
    return await fx(request).intraday(include_stocks=False)


@router.get("/pairs/{pair}")
async def forex_pair(request: Request, pair: str, timeframe: Timeframe = Query("15m")):
    return await fx(request).detail(pair, timeframe)


@router.get("/options/{pair}")
async def forex_options(request: Request, pair: str, expiry: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Currency option chain around the front-month future, with estimated greeks and which option to buy."""
    return await fx(request).option_chain(pair, expiry)


@router.get("/strategies/stats")
async def forex_strategy_stats(request: Request, timeframe: Timeframe = Query("15m")):
    return await fx(request).strategy_stats(timeframe)


@router.get("/patterns/stats")
async def forex_pattern_stats(request: Request, timeframe: Timeframe = Query("15m")):
    return await fx(request).pattern_stats(timeframe)


@router.get("/news/{pair}")
async def forex_news(request: Request, pair: str):
    inst = await fx(request).instrument(pair)
    if inst is None:
        raise HTTPException(404, f"{pair.upper()} is not one of the forex pairs ({', '.join(PAIRS)})")
    news: ForexNews = request.app.state.forex_news
    return await news.snapshot(inst)
