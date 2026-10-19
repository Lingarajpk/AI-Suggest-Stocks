"""Run the no-peeking check on real candles.

Usage (from backend/, with the backend running and Upstox connected):
    .venv/Scripts/python scripts/lookahead_check.py NIFTY50 5m
    .venv/Scripts/python scripts/lookahead_check.py RELIANCE 1d --base https://ai-suggest-stocks.onrender.com

Candles come from the backend's /api/stocks endpoint (completed candles), so no Upstox token is
needed here. Exit code 1 if anything uses later candles or repaints.
"""

import argparse
import sys
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.analysis import lookahead  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("timeframe", choices=["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w"])
    ap.add_argument("--base", default="http://localhost:8000", help="backend URL")
    ap.add_argument("--cuts", type=int, default=6)
    args = ap.parse_args()

    r = httpx.get(f"{args.base}/api/stocks/{args.symbol}", params={"timeframe": args.timeframe}, timeout=180)
    r.raise_for_status()
    candles = r.json()["candles"]
    df = pd.DataFrame(candles)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")[["open", "high", "low", "close", "volume"]].astype(float)
    print(f"{args.symbol} {args.timeframe}: {len(df)} completed candles, {args.cuts} cut points")
    notes: list[str] = []
    issues = lookahead.check(df, args.timeframe, cuts=args.cuts, notes=notes)
    for line in notes:
        print(" note:", line)
    for line in issues:
        print(" -", line)
    print("OK: nothing uses later candles." if not issues else f"{len(issues)} problem(s) found.")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
