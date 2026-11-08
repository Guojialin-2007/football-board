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
        "1012": {"outcomes": {"13": {"players": series([2.60, 2.30, 2.02])}, "14": {"players": series([1.50, 1.62, 1.80])}}},
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

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self):
        self.start = NOW + timedelta(hours=10)
        self.kick = {}

    def past(self, fid):
        return self.kick[fid]

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
        if path == "/v4/fixtures" and params.get("statusId") == 2:
            a = datetime.fromisoformat(params["from"].replace("Z", "+00:00"))
            if a < NOW - timedelta(days=15):
                return R([])
            rng = random.Random(params["from"])
            rows = []
            for k in range(20):
                st = a + timedelta(hours=7 * k + 3)
                g = rng.randint(0, 6)
                self.kick[f"p{params['from'][:10]}{k}"] = st
                rows.append({"fixtureId": f"p{params['from'][:10]}{k}", "participant1Name": "Arsenal",
                             "participant2Name": "Chelsea", "startTime": iso(st), "tournamentId": 17,
                             "participant1Score": g // 2 + (g % 2), "participant2Score": g // 2})
            return R(rows)
        if path == "/v4/fixtures":
            return R([{"fixtureId": "id1", "participant1Name": "Arsenal FC", "participant2Name": "Chelsea",
                       "startTime": iso(self.start), "tournamentId": 17},
                      {"fixtureId": "id2", "participant1Name": "Unknown Town", "participant2Name": "Nowhere FC",
                       "startTime": iso(self.start + timedelta(hours=2)), "tournamentId": 99}])
        if path == "/v4/historical-odds":
            fid = params["fixtureId"]
            if fid.startswith("p"):                       # a finished match: same moves, kickoff in the past
                return R(hist(self.past(fid)))
            return R(hist(self.start) if fid == "id1" else {"bookmakers": {}})
        raise AssertionError(url)


def test_board_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        B.DATA, B.SITE = os.path.join(d, "data"), os.path.join(d, "site")
        fs = FakeSession()
        os.environ["ODDSPAPI_KEY"] = "test-key"
        os.environ["ONLY_TOP"] = "0"
        os.environ["FD_MIN"] = "0"
        orig_papi, orig_fetch = B.Papi, T.fetch_results
        B.Papi = lambda: orig_papi(session=fs, sleep=lambda s: None)
        B.T.fetch_results = lambda data_dir, days=40, log=print: orig_fetch(data_dir, days=40, session=fs, sleep=lambda s: None, log=log)
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
        h = json.load(open(os.path.join(B.SITE, "history.json")))
        assert h["n"] >= 20 and "1" in h["ou"] and "1" in h["ah"], h        # +1 step both markets in the fake data
        g = h["ou"]["1"]
        assert g["follow"] is not None and g["fade"] is not None and g["open"] < g["close"]
        print(json.dumps(a["flags"], ensure_ascii=False, indent=1))


def test_same_team():
    assert T.same_team("Arsenal FC", "Arsenal") and T.same_team("Man Utd", "Manchester United")
    assert not T.same_team("Manchester United", "Manchester City") and not T.same_team("Real Madrid", "Real Sociedad")
    assert not T.same_team("Arsenal Women", "Arsenal")


def test_asian_settlement():
    from board import history as H
    assert abs(H.settle_over(3, 2.5, 1.9) - 0.9) < 1e-9 and H.settle_over(2, 2.5, 1.9) == -1
    assert H.settle_over(3, 2.75, 2.0) == 0.5                   # half win (2.5 wins, 3.0 pushes)
    assert H.settle_under(3, 2.75, 2.0) == -0.5                 # half loss
    assert H.settle_home(1, -0.75, 2.0) == 0.5                  # win by 1 on -0.75: half win
    assert H.settle_away(1, -0.75, 2.0) == -0.5
    assert H.settle_home(0, 0, 1.9) == 0                        # draw on level ball: push
    assert H.ah_name(-0.5) == "主让半球" and H.ah_name(0.25) == "主受让平半" and H.ah_name(0) == "平手"


def test_polymarket_leagues():
    from board import polyleagues as P

    class G:
        headers = {}

        def get(self, url, params=None, timeout=None):
            if url.endswith("/tags/slug/soccer"):
                return R({"id": 100350})
            if params.get("closed") == "false" or params.get("offset", 0) > 0:
                return R([])
            evs = []
            for i, (a, b) in enumerate((("Arsenal", "Chelsea"), ("Liverpool", "Everton"), ("Fulham", "Brentford"))):
                evs.append({"id": f"e{i}", "title": f"{a} vs. {b}", "volume": 1e6, "endDate": iso(NOW - timedelta(days=3)),
                            "series": [{"title": "English Premier League"}]})
            for i, (a, b) in enumerate((("Real Madrid", "Sevilla"), ("Barcelona", "Getafe"))):
                evs.append({"id": f"s{i}", "title": f"{a} vs. {b} - More Markets", "volume": 2e5,
                            "endDate": iso(NOW - timedelta(days=3)), "tags": [{"label": "Soccer"}, {"label": "La Liga"}]})
            return R(evs)

    top = P.rank(session=G(), log=lambda *a: None)
    assert [x["league"] for x in top] == ["English Premier League", "La Liga"] and top[0]["volume"] == 3000000
    fx = [{"tournamentId": 17, "participant1Name": "Arsenal FC", "participant2Name": "Chelsea FC"},
          {"tournamentId": 17, "participant1Name": "Everton", "participant2Name": "Fulham"},
          {"tournamentId": 8, "participant1Name": "Real Madrid CF", "participant2Name": "Getafe CF"},
          {"tournamentId": 99, "participant1Name": "Boca Juniors", "participant2Name": "River Plate"}]
    tm = P.assign(top, fx, {}, log=lambda *a: None)
    assert tm == {"17": "English Premier League", "8": "La Liga"}


def test_ideas():
    from board import ideas as I
    body = ("### 比赛ID\n\nid1\n\n### 比赛\n\nA vs B\n\n### 玩法\n\n大小球\n\n### 选择\n\n大\n\n### 盘口\n\n2.75\n\n"
            "### 赔率\n\n_No response_\n\n### 理由（可多选）\n\n- [X] 盘口升降\n- [ ] 历史同赔\n- [x] 直觉\n\n### 思路\n\n升档跟进")
    f = I.parse(body)
    assert f["match"] == "id1" and f["market"] == "大小球" and f["line"] == 2.75 and f["odds"] is None
    assert f["tags"] == ["盘口升降", "直觉"] and f["why"] == "升档跟进"
    assert I.settle(dict(f, odds=2.0), 2, 1)["word"] == "赢半"            # 3 goals on over 2.75
    assert I.settle({"market": "亚盘", "pick": "客队", "line": -0.5, "odds": 1.9}, 1, 1)["word"] == "赢"

    class FakeGH:
        ok = True
        replies = []

        def ensure_label(self):
            pass

        def issues(self):
            return [{"number": 7, "body": body, "html_url": "u", "user": {"login": "me"}, "created_at": iso(NOW - timedelta(days=2))},
                    {"number": 8, "body": body.replace("2.75", "3.5"), "html_url": "u", "user": {"login": "me"},
                     "created_at": iso(NOW - timedelta(hours=1))}]

        def reply_and_close(self, num, text, label):
            self.replies.append((num, label))

    with tempfile.TemporaryDirectory() as d:
        cards = {"id1": {"id": "id1", "start": int((NOW - timedelta(days=1)).timestamp()), "home": "A", "away": "B",
                         "league": "英超", "books": {}}}
        g = FakeGH()
        out = I.run(cards, lambda idea: (2, 1), d, d, gh=g, log=lambda *a: None)
        assert out["all"]["n"] == 1 and out["all"]["win"] == 1                 # #8 came after kickoff: not counted
        assert dict(g.replies) == {7: "✅正确", 8: "❌错误"}
        assert out["tags"]["盘口升降"]["n"] == 1


def test_football_data_history():
    from board import detail as Dt
    from board import fd as F
    csv_main = ("Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,PSH,PSD,PSA,PSCH,PSCD,PSCA,AHh,PAHH,PAHA,AHCh,PCAHH,PCAHA,P>2.5,P<2.5\n"
                "E0,10/08/2024,15:00,Arsenal,Chelsea,2,1,H,2.10,3.40,3.60,2.00,3.50,3.90,-0.5,1.95,1.95,-0.75,1.92,1.98,1.90,1.95\n"
                "E0,12/01/2025,15:00,Chelsea,Arsenal,0,0,D,2.60,3.30,2.80,2.70,3.30,2.70,0,1.95,1.95,0,1.90,2.00,2.0,1.85\n"
                "E0,03/03/2025,15:00,Arsenal,Fulham,3,0,H,2.12,3.38,3.55,1.80,3.80,4.60,-0.5,1.90,2.00,-0.75,1.95,1.95,1.8,2.0\n"
                "E0,05/04/2025,15:00,Man United,Arsenal,1,1,D,3.00,3.30,2.40,3.0,3.3,2.4,0.25,1.9,2.0,0.25,1.9,2.0,1.9,1.9\n")
    rows = F.parse_csv(csv_main)
    assert len(rows) == 4 and rows[0]["o"] == [2.1, 3.4, 3.6] and rows[0]["ah"] == -0.5 and rows[0]["ahc"] == -0.75
    csv_new = "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA\nBrazil,Serie A,2024,14/04/2024,20:00,Flamengo,Palmeiras,1,0,H,2.1,3.2,3.6\n"
    assert F.parse_csv(csv_new, extra=True)[0]["c"] == [2.1, 3.2, 3.6]
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "fd"))
        json.dump(rows, open(os.path.join(d, "fd", "E0_2425.json"), "w"))
        fdx = F.FD(d)
        assert fdx.team("Arsenal FC", "英超") == "Arsenal" and fdx.team("Manchester United", "英超") == "Man United"
        start = int(NOW.timestamp())
        fx = {"home": "Arsenal FC", "away": "Chelsea FC", "league": "英超", "start": start,
              "books": {"pinnacle": {"1x2": {"open": {"odds": [2.11, 3.4, 3.6]}, "now": {"odds": [2.0, 3.5, 3.9],
                                                                                  "p": [0.47, 0.28, 0.25]}},
                                     "ah": {"open": {"line": -0.5}, "now": {"line": -0.75, "p_home": 0.5}}}}}
        det = Dt.build(fx, T.Teams([]), [], fdx)
        assert det["h2h"]["n"] == 2 and det["h2h"]["w"] == 1 and det["h2h"]["d"] == 1     # Arsenal's view
        so = det["same_odds"]
        assert so["home_team"]["n"] == 2 and so["league"]["n"] == 2                           # 10/08 and 03/03 match
        assert det["same_move"]["ah"]["n"] == 2 and det["same_move"]["ah"]["home_k"] == 2     # -0.75: 2-1 half win, 3-0 win
        assert all(j["conf"] == "低" for j in det["judge"]) and det["judge"][0]["ci"]


def test_lambda_inverse():
    for lam in (1.8, 2.6, 3.4):
        assert abs(O.lam_from(2.5, O.pois_over(2.5, lam)) - lam) < 0.01


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("PASS", n)
