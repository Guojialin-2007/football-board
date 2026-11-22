"""Probe free sources (run in GitHub Actions): ESPN scoreboard odds, football-data fixtures CSVs."""
import csv, io, json, requests
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"
for lg in ["eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions", "usa.1", "mex.1", "bra.1", "jpn.1", "ksa.1", "eng.2", "ned.1", "por.1", "tur.1", "arg.1", "aus.1", "kor.1"]:
    try:
        r = s.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard", params={"dates": "20261122-20261130"}, timeout=30)
        ev = r.json().get("events", [])
        n_odds = 0; ex = None
        for e in ev:
            for c in e.get("competitions", []):
                if c.get("odds"):
                    n_odds += 1; ex = ex or c["odds"][0]
        print("ESPN", lg, r.status_code, "events", len(ev), "with odds", n_odds)
        if ex: print("   sample odds:", json.dumps(ex)[:700])
    except Exception as e:
        print("ESPN", lg, "ERR", type(e).__name__, str(e)[:100])
for u in ["https://www.football-data.co.uk/fixtures.csv", "https://www.football-data.co.uk/new_league_fixtures.csv"]:
    try:
        r = s.get(u, timeout=30); print("FD", u, r.status_code, len(r.content))
        rows = list(csv.DictReader(io.StringIO(r.content.decode("latin-1").lstrip("﻿"))))
        if rows:
            print("   cols:", list(rows[0].keys())[:80])
            from collections import Counter
            print("   leagues:", Counter((x.get("Div") or x.get("Country", "") + "/" + x.get("League", "")) for x in rows))
            print("   dates:", sorted({x.get("Date") for x in rows})[:12])
            print("   sample:", {k: v for k, v in rows[0].items() if v}.__repr__()[:600])
    except Exception as e:
        print("FD", u, "ERR", type(e).__name__, str(e)[:100])
