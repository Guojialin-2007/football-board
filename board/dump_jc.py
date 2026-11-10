"""Debug: can the public China Sports Lottery (竞彩) odds endpoints be reached from here, and what do they return."""
import json
import sys

import requests

URLS = {
    "calc_all": "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?poolCode=hhad,had,crs,ttg,hafu&channel=c",
    "list": "https://webapi.sporttery.cn/gateway/jc/football/getMatchListV1.qry?clientCode=3001",
}
H = {"User-Agent": "Mozilla/5.0 (football-board research)", "Referer": "https://www.sporttery.cn/"}
for k, u in URLS.items():
    try:
        r = requests.get(u, headers=H, timeout=30)
        print(k, r.status_code, len(r.content), file=sys.stderr)
        try:
            js = r.json()
            v = js.get("value") or {}
            ml = v.get("matchInfoList") or v.get("matchList") or []
            print(k, "keys", list(js.keys())[:10], "value keys", list(v.keys())[:10] if isinstance(v, dict) else type(v))
            print(json.dumps(ml[:1], ensure_ascii=False)[:3500])
        except ValueError:
            print(k, r.text[:500])
    except requests.RequestException as e:
        print(k, "ERR", type(e).__name__, str(e)[:200])
