"""End-to-end with fake OddsPapi + ESPN: classify markets, compact history, movement, stats, flags, data.json."""
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from board import build as B  # noqa: E402
from board import odds as O  # noqa: E402
from board import stats as T  # noqa: E402

NOW = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.000Z")

MARKETS = [
    {"marketId": 101, "sportId": 10, "marketName": "Full Time Result", "marketType": "1x2", "period": "fulltime",
     "outcomes": [{"outcomeId": 1, "outcomeName": "1"}, {"outcomeId": 2, "outcomeName": "X"}, {"outcomeId": 3, "outcomeName": "2"}]},
    {"marketId": 1010, "sportId": 10, "marketName": "Over Under Full Time", "marketType": "totals", "period": "fulltime",
     "handicap": 2.5, "outcomes": [{"outcomeId": 11, "outcomeName": "Over"}, {"outcomeId": 12, "outcomeName": "Under"}]},
    {"marketId": 1012, "sportId": 10, "marketName": "Over Under Full Time", "marketType": "totals", "period": "fulltime",
     "handicap": 2.75, "outcomes": [{"outcomeId": 13, "outcomeName": "Over"}, {"outcomeId": 14, "outcomeName": "Under"}]},
    {"marketId": 1014, "sportId": 10, "marketName": "Over Under 1st Half", "marketType": "totals", "period": "fulltime",
     "handicap": 1.5, "outcomes": [{"outcomeId": 15, "outcomeName": "Over"}, {"outcomeId": 16, "outcomeName": "Under"}]},
    {"marketId": 1068, "sportId": 10, "marketName": "Asian Handicap", "marketType": "spreads", "period": "fulltime",
     "handicap": -0.5, "outcomes": [{"outcomeId": 21, "outcomeName": "1"}, {"outcomeId": 22, "outcomeName": "2"}]},
    {"marketId": 1070, "sportId": 10, "marketName": "Asian Handicap", "marketType": "spreads", "period": "fulltime",
     "handicap": -0.75, "outcomes": [{"outcomeId": 23, "outcomeName": "1"}, {"outcomeId": 24, "outcomeName": "2"}]},
]


def hist(start):
    """Pinnacle: over 2.5 shortens 2.10 -> 1.80 (more goals expected), home AH -0.5 1.95 -> -0.75 1.92."""
    def series(prices):
        return {"0": [{"createdAt": iso(start - timedelta(hours=30 - 6 * i)), "price": p, "active": True}
                      for i, p in enumerate(prices)]}
    pin = {"markets": {
        "101": {"outcomes": {"1": {"players": series([2.30, 2.20, 2.05])}, "2": {"players": series([3.30, 3.35, 3.40])},
                             "3": {"players": series([3.20, 3.40, 3.80])}}},
        "1010": {"outcomes": {"11": {"players": series([2.10, 1.95, 1.80])}, "12": {"players": series([1.78, 1.90, 2.08])}}},
        "1014": {"outcomes": {"15": {"players": series([2.0, 2.0, 2.0])}, "16": {"players": series([1.8, 1.8, 1.8])}}},
        "1068": {"outcomes": {"21": {"players": series([1.95, 1.85, 1.75])}, "22": {"players": series([1.95, 2.05, 2.15])}}},
        "1070": {"outcomes": {"23": {"players": series([2.20, 2.05, 1.92])}, "24": {"players": series([1.72, 1.82, 1.96])}}}}}
    soft = {"markets": {"101": {"outcomes": {"1": {"players": series([2.30, 2.25, 2.25])},
                                             "2": {"players": series([3.2, 3.2, 3.2])},
                                             "3": {"players": series([3.2, 3.3, 3.5])}}}}}
    return {"bookmakers": {"pinnacle": pin, "bet365": soft}}


class R:
    def __init__(self, js, code=200):
        self.js, self.status_code, self.text = js, code, json.dumps(js)[:200]

    def json(self):
        return self.js


class FakeSession:
    def __init__(self):
        self.start = NOW + timedelta(hours=10)

    def get(self, url, params=None, timeout=None):
        params = params or {}
        if "espn.com" in url:
            day = params.get("dates")
            rng = random.Random(day)
            evs = []
            for k, (h, a) in enumerate((("Arsenal", "Chelsea"), ("Chelsea", "Fulham"), ("Fulham", "Arsenal"))):
                evs.append({"id": f"{day}{k}", "date": f"{day[:4]}-{day[4:6]}-{day[6:]}T15:00Z", "leagueId": "1",
                            "competitions": [{"status": {"type": {"completed": True}}, "competitors": [
                                {"homeAway": "home", "score": str(rng.randint(0, 4)), "team": {"displayName": h}},
                                {"homeAway": "away", "score": str(rng.randint(0, 3)), "team": {"displayName": a}}]}]})
            return R({"events": evs if int(day) % 3 == 0 else [], "leagues": [{"id": "1", "name": "Premier League"}]})
        path = url.split("oddspapi.io")[1]
        if path == "/v4/sports":
            return R([{"sportId": 10, "sportName": "Soccer"}])
        if path == "/v4/markets":
            return R(MARKETS)
        if path == "/v4/tournaments":
            return R([{"tournamentId": 17, "tournamentName": "Premier League", "categoryName": "England"}])
        if path == "/v4/fixtures":
            return R([{"fixtureId": "id1", "participant1Name": "Arsenal FC", "participant2Name": "Chelsea",
                       "startTime": iso(self.start), "tournamentId": 17},
                      {"fixtureId": "id2", "participant1Name": "Unknown Town", "participant2Name": "Nowhere FC",
                       "startTime": iso(self.start + timedelta(hours=2)), "tournamentId": 99}])
        if path == "/v4/historical-odds":
            return R(hist(self.start) if params["fixtureId"] == "id1" else {"bookmakers": {}})
        raise AssertionError(url)


def test_board_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        B.DATA, B.SITE = os.path.join(d, "data"), os.path.join(d, "site")
        fs = FakeSession()
        os.environ["ODDSPAPI_KEY"] = "test-key"
        orig_papi, orig_fetch = B.Papi, T.fetch_results
        B.Papi = lambda: orig_papi(session=fs, sleep=lambda s: None)
        B.T.fetch_results = lambda data_dir, log=print: orig_fetch(data_dir, days=40, session=fs, sleep=lambda s: None, log=log)
        out = B.run(log=lambda *a: None)
        B.Papi, B.T.fetch_results = orig_papi, orig_fetch
        cls = O.classify(MARKETS, 10)
        assert {v["kind"] for v in cls.values()} == {"1x2", "ou", "ah"} and "1014" not in cls   # 1st-half excluded
        fx = {f["id"]: f for f in out["fixtures"]}
        a = fx["id1"]
        pin = a["books"]["pinnacle"]
        assert pin["ou"]["open"]["lam"] < pin["ou"]["now"]["lam"]                 # over shortened -> more goals
        assert pin["ah"]["now"]["sup"] > pin["ah"]["open"]["sup"]                 # money on the home side
        assert pin["1x2"]["now"]["p"][0] > pin["1x2"]["open"]["p"][0]
        kinds = {f["k"] for f in a["flags"]}
        assert {"ou", "ah", "1x2"} <= kinds, a["flags"]
        assert "value" in kinds                                                    # bet365 away 3.5 vs fair
        assert a["stats"]["home"]["all"]["team"] == "Arsenal" and a["stats"]["home"]["all"]["n"] > 0
        assert a["league"] == "Premier League" and a["country"] == "England"
        assert fx["id2"]["books"] == {} and fx["id2"]["stats"]["home"]["all"] is None
        assert out["summary"]["with_odds"] == 1
        json.load(open(os.path.join(B.SITE, "data.json")))
        print(json.dumps(a["flags"], ensure_ascii=False, indent=1))


def test_same_team():
    assert T.same_team("Arsenal FC", "Arsenal") and T.same_team("Man Utd", "Manchester United")
    assert not T.same_team("Manchester United", "Manchester City") and not T.same_team("Real Madrid", "Real Sociedad")
    assert not T.same_team("Arsenal Women", "Arsenal")


def test_lambda_inverse():
    for lam in (1.8, 2.6, 3.4):
        assert abs(O.lam_from(2.5, O.pois_over(2.5, lam)) - lam) < 0.01


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("PASS", n)
