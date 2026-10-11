import math
from datetime import datetime, timedelta

from app import news
from app.market_session import IST

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=IST)

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Infosys Q2 profit jumps 12%, beats estimates - Economic Times</title>
  <link>https://news.google.com/a</link><pubDate>Fri, 09 Oct 2026 04:00:00 GMT</pubDate>
  <source url="https://economictimes.com">Economic Times</source></item>
<item><title>Sensex ends flat as metals drag - Mint</title>
  <link>https://news.google.com/b</link><pubDate>Fri, 09 Oct 2026 05:00:00 GMT</pubDate><source>Mint</source></item>
<item><title>Infosys wins $1 billion order - Moneycontrol</title>
  <link>https://news.google.com/c</link><pubDate>Mon, 01 Jan 2024 05:00:00 GMT</pubDate><source>Moneycontrol</source></item>
</channel></rss>"""


def test_parse_rss_keeps_relevant_recent_items():
    items = news.parse_rss(RSS, "INFY", news.aliases_for("INFY", "Infosys"), NOW - timedelta(hours=48))
    assert [i["title"] for i in items] == ["Infosys Q2 profit jumps 12%, beats estimates"]  # off-topic and old dropped
    assert items[0]["publisher"] == "Economic Times"
    assert datetime.fromisoformat(items[0]["published"]).tzinfo is not None
    assert news.parse_rss("not xml", "INFY", ["Infosys"], NOW) == []


def test_keyword_scores_direction_and_impact():
    good = news.keyword_score("Infosys Q2 profit jumps 12%, beats estimates")
    bad = news.keyword_score("SEBI probe: Bajaj Finance shares plunge after fraud allegation")
    flat = news.keyword_score("Infosys to hold board meeting next week")
    assert good["sentiment"] > 0.5 and good["impact"] == "high"
    assert bad["sentiment"] < -0.5 and bad["impact"] == "high"
    assert flat["sentiment"] == 0 and flat["impact"] == "low"


def test_llm_output_parsing_is_strict():
    text = 'Sure: [{"i":1,"sentiment":0.8,"impact":"high","reason":"beat"},{"i":2,"sentiment":-3,"impact":"huge"}]'
    out = news.parse_llm_scores(text, 2)
    assert out[0]["sentiment"] == 0.8 and out[1]["sentiment"] == -1.0 and out[1]["impact"] == "medium"
    assert news.parse_llm_scores('[{"i":1,"sentiment":0.5}]', 2) is None  # missing a headline -> fall back
    assert news.parse_llm_scores("no json here", 1) is None


def test_aggregate_decays_and_weights_impact():
    fresh = {"published": (NOW - timedelta(hours=1)).isoformat(), "sentiment": 0.8, "impact": "high"}
    old = {"published": (NOW - timedelta(hours=72)).isoformat(), "sentiment": -0.8, "impact": "high"}
    a = news.aggregate([fresh, old], NOW)
    assert a["score"] > 0.3 and a["label"].lower().endswith("positive")
    assert news.aggregate([], NOW)["score"] == 0.0
    one_low = news.aggregate([{**fresh, "impact": "low"}], NOW)
    assert one_low["score"] < a["score"]  # a single low-impact headline barely moves it


def test_adjustment_is_capped():
    assert math.isclose(news.adjust(0.5, 1.0, news.DEFAULT_BETA, 10), 0.6, abs_tol=1e-6)
    assert news.adjust(0.5, 1.0, 5.0, 10) == 0.6  # huge weight still capped at +10 pts
    assert abs(news.adjust(0.7, -1.0, 5.0, 10) - 0.6) < 1e-9
    assert news.adjust(0.55, 0.0, news.DEFAULT_BETA, 10) == 0.55


def test_fit_beta_learns_sign_from_outcomes():
    import random

    rnd = random.Random(0)
    samples = []
    for _ in range(400):
        x = rnd.uniform(-1, 1)
        p_true = 1 / (1 + math.exp(-1.5 * x))
        samples.append((0.5, x, 1.0 if rnd.random() < p_true else 0.0))
    assert news.fit_beta(samples) > 0.8
    flipped = [(p, -x, y) for p, x, y in samples]
    assert news.fit_beta(flipped) < -0.8


def test_store_roundtrip(tmp_path):
    st = news.NewsStore(tmp_path / "n.db")
    row = {"id": "x1", "symbol": "INFY", "title": "t", "publisher": "p", "link": "l", "published": NOW.isoformat(),
           "fetched_at": NOW.isoformat(), "sentiment": 0.5, "impact": "high", "reason": "r", "scored_by": "keywords",
           "price_at": 100.0, "p_tech": 0.55, "source": "upstox"}
    st.add([row, row])
    assert st.known(["x1", "x2"]) == {"x1"}
    assert len(st.recent("INFY", NOW - timedelta(days=1))) == 1
    assert len(st.pending_outcomes("upstox")) == 1
    st.set_outcome("x1", 1.0, 2.5)
    assert st.learning_samples("upstox")[0]["outcome_ret"] == 2.5
    st.close()
