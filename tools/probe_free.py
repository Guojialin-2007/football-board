import json, requests
from collections import Counter, defaultdict
s = requests.Session()
by = defaultdict(Counter)
for d in ["20261122", "20261123", "20261125", "20261126", "20261128", "20261129"]:
    r = s.get("https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard", params={"dates": d, "limit": 1000}, timeout=30)
    for e in r.json()["events"]:
        c = e["competitions"][0]
        o = c.get("odds") or []
        pv = ((o[0] or {}).get("provider") or {}).get("name", "?") if o and o[0] else "none"; o = [x for x in o if x]
        kinds = "+".join(k for k in ("moneyline", "total", "pointSpread") if o and o[0].get(k)) or ("1x2" if o and o[0].get("homeTeamOdds") else "-")
        by[(e.get("season") or {}).get("slug", "?")][pv + ":" + kinds] += 1
for k in sorted(by): print("LG", k, dict(by[k]))
