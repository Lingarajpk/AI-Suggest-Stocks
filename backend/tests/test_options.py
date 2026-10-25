from app import options as opt


def leg(ltp, oi, prev_oi, delta, gamma=0.0005, iv=13.0, bid=None, ask=None):
    return {
        "instrument_key": "X",
        "market_data": {"ltp": ltp, "close_price": ltp, "oi": oi, "prev_oi": prev_oi, "volume": 10,
                        "bid_price": bid if bid is not None else ltp - 0.1, "ask_price": ask if ask is not None else ltp + 0.1},
        "option_greeks": {"delta": delta, "gamma": gamma, "theta": -5.0, "vega": 10.0, "iv": iv},
    }


def chain(spot=22000.0, put_oi=1.0, call_oi=1.0):
    rows = []
    for k in range(-5, 6):
        strike = 22000 + 50 * k
        cd = max(0.05, min(0.95, 0.5 - k * 0.08))
        rows.append({
            "strike_price": strike, "underlying_spot_price": spot,
            "call_options": leg(max(1.0, 120 - 40 * k), 1e5 * call_oi * (1 + max(k, 0)), 9e4, cd),
            "put_options": leg(max(1.0, 120 + 40 * k), 1e5 * put_oi * (1 + max(-k, 0)), 9e4, cd - 1),
        })
    return rows


def test_parse_and_summary():
    rows, spot = opt.parse_chain(chain(put_oi=1.5))
    assert spot == 22000.0 and len(rows) == 11 and rows[0]["strike"] < rows[-1]["strike"]
    s = opt.summarize(rows, spot)
    assert s["atm"] == 22000 and s["pcr"] > 1.2 and s["bias"] == "bullish"
    assert s["call_wall"] == 22250 and s["put_wall"] == 21750
    assert s["straddle"] == 240.0


def test_max_pain_is_between_the_walls():
    rows, _ = opt.parse_chain(chain())
    mp = opt.max_pain(rows)
    assert 21750 <= mp <= 22250


def test_pick_call_near_atm_with_rupees_per_lot():
    rows, spot = opt.parse_chain(chain())
    p = opt.pick_option(rows, spot, "CE", target=22100, stop=21950, lot_size=75)
    assert p["kind"] == "CE" and abs(p["strike"] - spot) <= 100
    assert p["target"] > p["entry"] > p["stop"] > 0
    assert p["profit_per_lot"] == round((p["target"] - p["entry"]) * 75, 2)
    assert p["loss_per_lot"] == round((p["entry"] - p["stop"]) * 75, 2)


def test_pick_put_gains_when_index_falls():
    rows, spot = opt.parse_chain(chain())
    p = opt.pick_option(rows, spot, "PE", target=21900, stop=22050, lot_size=75)
    assert p["kind"] == "PE" and p["target"] > p["entry"] > p["stop"]


def test_recommend_follows_index_call_and_flags_opposing_oi():
    rows, spot = opt.parse_chain(chain(call_oi=1.6))  # call-heavy chain: bearish OI
    s = opt.summarize(rows, spot)
    card = {"verdict": "BUY", "reason": "both up", "atr": 20.0,
            "levels": {"target": 22050.0, "stop": 21970.0}}
    r = opt.recommend(rows, spot, card, s, 75)
    assert r["action"] == "BUY CE" and r["pick"]["kind"] == "CE" and r["against_oi"] is True
    wait = opt.recommend(rows, spot, {"verdict": "WAIT", "reason": "no trend", "atr": 20.0}, s, 75)
    assert wait["action"] == "WAIT" and wait["pick"] is None and set(wait["candidates"]) == {"CE", "PE"}


def test_window_centres_on_atm():
    rows, spot = opt.parse_chain(chain())
    w = opt.window(rows, spot, n=2)
    assert [r["strike"] for r in w] == [21900, 21950, 22000, 22050, 22100]


def test_buildup_classification_and_flow():
    from app import options as opt

    assert opt.classify(5, 100) == "long_buildup" and opt.classify(-5, 100) == "short_buildup"
    assert opt.classify(5, -100) == "short_covering" and opt.classify(-5, -100) == "long_unwinding"
    assert opt.classify(0, 100) is None and opt.classify(5, None) is None

    def leg(chg_price, chg_oi):
        return {"buildup": opt.classify(chg_price, chg_oi), "oi_change": chg_oi}

    # Heavy put writing (puts fall, OI up) and call short covering: bullish.
    rows = [{"strike": 100.0 + i, "call": leg(2, -50), "put": leg(-3, 400)} for i in range(5)]
    flow = opt.oi_flow(rows, 102.0)
    assert flow["lean"] == "bullish" and flow["score"] == 1.0 and flow["top"][0]["side"] == "PE"
    # Call writing dominates: bearish.
    rows = [{"strike": 100.0 + i, "call": leg(-2, 500), "put": leg(-1, 50)} for i in range(5)]
    assert opt.oi_flow(rows, 102.0)["lean"] == "bearish"
    assert opt.oi_flow([{"strike": 100.0, "call": None, "put": None}], 100.0) is None


def test_iv_percentile():
    from app import options as opt

    hist = {f"2026-01-{d:02d}": 10.0 + d for d in range(1, 26)}  # 11 .. 35
    r = opt.iv_percentile(hist, 23.0)
    assert r["rank"] == 50 and r["percentile"] == 48 and r["days"] == 25
    assert opt.iv_percentile({"2026-01-01": 12.0}, 12.0) is None
