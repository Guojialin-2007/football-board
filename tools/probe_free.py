import json, requests
s = requests.Session()
r = s.get("https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard", params={"dates": "20261128", "limit": 1000}, timeout=30)
ev = r.json()["events"]
seen = set()
for e in ev:
    c = e["competitions"][0]
    o = (c.get("odds") or [None])[0]
    if not o: continue
    pv = o["provider"]["name"]
    if pv in seen: continue
    seen.add(pv)
    def strip(x):
        if isinstance(x, dict): return {k: strip(v) for k, v in x.items() if k not in ("link", "links", "logos", "logo", "team")}
        if isinstance(x, list): return [strip(v) for v in x]
        return x
    print("EVENT", e["id"], e["date"], e["name"], "| league:", json.dumps(e.get("league") or e.get("season"))[:200], "| comp keys", list(c.keys()))
    print("   competitors:", [(t["homeAway"], t["team"]["displayName"], t["team"]["id"]) for t in c["competitors"]])
    print("   ODDS", pv, json.dumps(strip(o))[:3000])
from collections import Counter
print("providers", Counter((e["competitions"][0].get("odds") or [{}])[0].get("provider", {}).get("name") for e in ev))
print("uid leagues", Counter(e["uid"].split("~")[1] for e in ev).most_common(80))
