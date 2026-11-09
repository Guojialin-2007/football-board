"""Debug: which bookmaker slugs return odds on the free /v4/historical-odds endpoint (no allowance used)."""
import json
import os

from .papi import Papi

CANDIDATES = ["bet365", "1xbet", "williamhill", "betway", "unibet", "bwin", "betfair-ex", "betfair", "marathonbet",
              "888sport", "betvictor", "sbobet", "ladbrokes", "coral", "paddypower", "skybet", "betfred", "betsson",
              "nordicbet", "betano", "stake", "pinnacle", "betonline", "bovada", "draftkings", "fanduel", "betmgm",
              "matchbook", "smarkets", "betdaq", "dafabet", "188bet", "12bet", "cloudbet", "22bet", "melbet", "mostbet",
              "parimatch", "ggbet", "betcris", "unibet_uk", "tipico", "interwetten", "betclic", "winamax", "sportingbet"]

api = Papi()
fx = json.load(open(os.path.join("data", "fixtures.json")))["rows"]
fid = fx[0]["fixtureId"]
ok = []
for i in range(0, len(CANDIDATES), 3):
    chunk = CANDIDATES[i:i + 3]
    try:
        js = api.get("/v4/historical-odds", fixtureId=fid, bookmakers=",".join(chunk))
    except RuntimeError as e:
        print(chunk, "error", str(e)[:80])
        continue
    root = next(iter(js.values())) if isinstance(js, dict) and len(js) == 1 and "bookmakers" not in js else js
    got = list(((root or {}).get("bookmakers") or {}).keys())
    print(chunk, "->", got)
    ok += got
print("AVAILABLE:", sorted(set(ok)))
