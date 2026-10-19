import pandas as pd
import pytest

from app.analysis import costs
from app.analysis.history import equity_curve, profit_factor, sharpe, simulate_trades, sqn, trade_stats
from app.analysis.indicators import compute


def test_round_trip_costs_per_product():
    # Rs 1 lakh each way. Intraday: brokerage 2 x 20, STT 25, exchange 5.94, SEBI 0.2, stamp 3, GST 8.31.
    assert costs.charges("intraday", 100_000, 100_000) == pytest.approx(82.45, abs=0.05)
    # Delivery: brokerage 40, STT 200, exchange 5.94, SEBI 0.2, stamp 15, DP 18.5, GST 11.64.
    assert costs.charges("delivery", 100_000, 100_000) == pytest.approx(291.28, abs=0.05)
    assert costs.round_trip_pct("intraday") == pytest.approx(0.0824 + 0.06, abs=0.001)
    assert costs.round_trip_pct("delivery") == pytest.approx(0.2913 + 0.10, abs=0.001)
    assert costs.round_trip_pct("futures") < costs.round_trip_pct("intraday") < costs.round_trip_pct("delivery")
    # Flat Rs 20 brokerage weighs more on a small position.
    assert costs.round_trip_pct("intraday", 10_000) > costs.round_trip_pct("intraday")


def test_product_for_timeframe_and_kind():
    assert costs.product_for("5m") == "intraday"
    assert costs.product_for("1d") == "delivery"
    assert costs.product_for("1w") == "delivery"
    assert costs.product_for("15m", "index") == "futures"


def trades_from(pnls, start="2026-01-01"):
    days = pd.bdate_range(start, periods=len(pnls) + 1)
    return [{"entry_time": days[i].isoformat(), "exit_time": days[i + 1].isoformat(), "pnl_pct": p, "gross_pnl_pct": p + 0.1,
             "cost_pct": 0.1} for i, p in enumerate(pnls)]


def test_profit_factor_sqn_and_drawdown():
    pnl = [2.0, -1.0, 3.0, -1.0, -1.0]
    assert profit_factor(pnl) == pytest.approx(5 / 3, abs=0.01)
    assert profit_factor([1.0, 2.0]) is None  # no losses
    assert sqn(pnl) == pytest.approx(0.4 / pd.Series(pnl).std() * 5 ** 0.5, abs=0.01)
    points, dd = equity_curve(trades_from([10.0, -10.0, -10.0, 30.0]))
    # 1.10 -> 0.99 -> 0.891 (peak 1.10: -19%) -> 1.1583
    assert dd == pytest.approx(-19.0, abs=0.01)
    assert points[-1][1] == pytest.approx(15.83, abs=0.01)


def test_trade_stats_are_net_with_gross_alongside():
    s = trade_stats(trades_from([1.0, -0.5, 0.4, -0.2, 0.3] * 5))
    assert s["count"] == 25 and s["wins"] == 15
    assert s["avg_pnl_pct"] == pytest.approx(0.2, abs=0.001)
    assert s["gross_avg_pnl_pct"] == pytest.approx(0.3, abs=0.001) and s["cost_pct"] == 0.1
    assert s["avg_win_pct"] > 0 > s["avg_loss_pct"]
    assert s["profit_factor"] > 1 and s["sharpe"] is not None and len(s["equity"]) == 25
    assert trade_stats([])["profit_factor"] is None


def test_sharpe_needs_enough_days():
    assert sharpe(trades_from([1.0, -0.5])) is None
    assert sharpe(trades_from([0.5, -0.2] * 15)) > 0


def test_simulate_trades_subtracts_cost():
    idx = pd.date_range("2026-01-05 09:15", periods=60, freq="15min", tz="Asia/Kolkata")
    close = pd.Series([100 + i * 0.5 for i in range(60)], index=idx)
    df = pd.DataFrame({"open": close - 0.2, "high": close + 0.6, "low": close - 0.6, "close": close, "volume": 1000.0}, index=idx)
    ind = compute(df, True)
    buy = pd.Series(False, index=idx)
    buy.iloc[30] = True
    sell = pd.Series(False, index=idx)
    gross, _ = simulate_trades(df, ind, buy, sell)
    net, _ = simulate_trades(df, ind, buy, sell, cost_pct=0.15)
    assert gross and net
    assert net[0]["gross_pnl_pct"] == gross[0]["pnl_pct"]
    assert net[0]["pnl_pct"] == pytest.approx(gross[0]["pnl_pct"] - 0.15, abs=0.011)
