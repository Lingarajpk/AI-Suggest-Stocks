import numpy as np
import pandas as pd

from app.analysis import metalabel as ml
from app.analysis import tearsheet as ts


def test_tearsheet_numbers():
    times = [f"2026-01-{d:02d}T10:00:00+05:30" for d in (5, 6, 7, 8, 9, 12, 13)]
    pnl = [2.0, -1.0, 3.0, -1.0, 1.0, -2.0, 4.0]
    sheet = ts.build(times, pnl, instruments=2)
    st = sheet["stats"]
    assert st["trades"] == 7 and st["total_return_pct"] == 3.0  # sum / 2 instruments
    assert st["max_drawdown_pct"] == -1.0  # 2.5 peak -> 1.5 after the -2 trade
    assert st["win_rate"] == 57.1 and st["profit_factor"] == 2.5
    assert sheet["monthly"] == [{"month": "2026-01", "pct": 3.0}]
    assert sum(b["count"] for b in sheet["distribution"]) == 7
    assert ts.build(times[:3], pnl[:3], 2) is None


def _rows(n, informative=True, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    t0 = pd.Timestamp("2025-01-01T09:15:00+05:30")
    for i in range(n):
        x = rng.normal(size=3)
        p_win = 1 / (1 + np.exp(-2 * x[0])) if informative else 0.4
        win = rng.uniform() < p_win
        entry = t0 + pd.Timedelta(hours=i)
        rows.append({"entry": entry.isoformat(), "exit": (entry + pd.Timedelta(minutes=30)).isoformat(),
                     "key": "a" if i % 2 else "b", "side": 1, "pnl": 1.0 if win else -0.8, "y": float(win), "x": x})
    return rows


def test_meta_label_learns_and_is_marked_useful():
    built = ml.build(_rows(1500), ["a", "b"], ["f0", "f1", "f2"])
    assert built and built["useful"]
    assert built["test"]["top_half_avg_pct"] > built["test"]["all_avg_pct"]
    assert built["test"]["top_half_win_pct"] > built["test"]["base_win_pct"]


def test_meta_label_rejects_noise():
    built = ml.build(_rows(1500, informative=False, seed=4), ["a", "b"], ["f0", "f1", "f2"])
    assert built is not None and not built["useful"]
    assert ml.score_current(built, pd.DataFrame([[0.0, 0.0, 0.0]], columns=["f0", "f1", "f2"]), []) == {}
