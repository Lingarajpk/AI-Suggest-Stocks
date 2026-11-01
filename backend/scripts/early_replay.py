"""Replay early signals on real candles: how much earlier, how often confirmed, and whether it pays after costs.

Usage (from backend/, with the backend running and Upstox connected):
    .venv/Scripts/python scripts/early_replay.py NIFTY50 BANKNIFTY RELIANCE
    .venv/Scripts/python scripts/early_replay.py NIFTY50 --base https://ai-suggest-stocks.onrender.com

Candles come from the backend's /api/stocks endpoint (1m, 5m, 15m completed candles); the 1-minute data covers
about the last two sessions. Uses the rule score (the combined model isn't replayed).
"""

import argparse
import sys
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.analysis import early  # noqa: E402
from app.analysis.costs import round_trip_pct  # noqa: E402


def frame(base: str, symbol: str, tf: str) -> pd.DataFrame:
    r = httpx.get(f"{base}/api/stocks/{symbol}", params={"timeframe": tf}, timeout=180)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["candles"])
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")[["open", "high", "low", "close", "volume"]].astype(float)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="backend URL")
    args = ap.parse_args()
    total = []
    for sym in args.symbols:
        index = sym.upper().startswith(("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"))
        cost = round_trip_pct("futures" if index else "intraday")
        res = early.replay(frame(args.base, sym, "5m"), frame(args.base, sym, "1m"), frame(args.base, sym, "15m"), cost)
        s = res["summary"]
        total += res["rows"]
        print(f"{sym}: {s['fired']} early signals in {s['bars']} candles | confirmed {s['confirmed_pct']}% | "
              f"{s['avg_minutes_earlier']} min earlier | entry edge when confirmed {s['avg_entry_edge_confirmed_pct']}% | "
              f"move when cancelled {s['avg_move_cancelled_pct']}% | net vs waiting {s['avg_net_vs_waiting_pct']}% per signal (cost {cost:.3f}%)")
    if total:
        conf = [r for r in total if r["status"] == "confirmed"]
        net = sum(r["net_vs_waiting_pct"] for r in total) / len(total)
        print(f"ALL: {len(total)} early signals, {100 * len(conf) / len(total):.0f}% confirmed, net vs waiting {net:+.3f}% per signal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
