"""Free fixtures + odds from ESPN's public scoreboard (no key, no allowance).

ESPN shows one bookmaker per match. For most big leagues it is DraftKings with an opening price and the current
price for the 1x2, the main over/under line and the main handicap line; some matches only have a bet365 1x2.
Every run stores the current price, so the board's own record builds a real price history between "open" and
"now" (data/espn_odds.json). Used next to OddsPapi: matches OddsPapi already covers are skipped.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard"
# board league name -> ESPN league code
CODES = {
    "英超": "eng.1", "澳超": "aus.1", "欧冠": "uefa.champions", "西甲": "esp.1", "意甲": "ita.1", "德甲": "ger.1",
    "法甲": "fra.1", "美职联": "usa.1", "墨西哥 Liga MX": "mex.1", "欧联杯": "uefa.europa", "欧协联": "uefa.europa.conf",
    "巴甲": "bra.1", "英冠": "eng.2", "沙特联": "ksa.1", "葡超": "por.1", "荷甲": "ned.1", "土超": "tur.1", "阿甲": "arg.1",
    "日职 J1": "jpn.1", "日职 J2": "jpn.2", "韩 K1": "kor.1", "韩 K2": "kor.2", "德乙": "ger.2", "法乙": "fra.2",
    "西乙": "esp.2", "欧国联": "uefa.nations",
}
COUNTRY = {"eng": "England", "aus": "Australia", "esp": "Spain", "ita": "Italy", "ger": "Germany", "fra": "France",
           "usa": "USA", "mex": "Mexico", "bra": "Brazil", "ksa": "Saudi Arabia", "por": "Portugal",
           "ned": "Netherlands", "tur": "Turkey", "arg": "Argentina", "jpn": "Japan", "kor": "Korea Republic",
           "uefa": "International"}


def american(x):
    """'-130' / '+110' / 'EVEN' -> decimal odds."""
    try:
        s = str(x).strip().upper()
        if s in ("EVEN", "EV", "PK"):
            return 2.0
        v = float(s)
    except (TypeError, ValueError):
        return None
    if v >= 100:
        return round(1 + v / 100, 4)
    if v <= -100:
        return round(1 + 100 / -v, 4)
    return None


def _line(x):
    try:
        return float(str(x).lstrip("ou").replace("+", ""))
    except (TypeError, ValueError):
        return None


def prices(odds):
    """One ESPN odds object -> (book, {"open": state, "now": state}) with state = {"1x2": [h, d, a],
    "ou": (line, over, under), "ah": (home line, home, away)} (keys missing when not offered)."""
    if not odds:
        return None, None
    book = ((odds.get("provider") or {}).get("name") or "espn").lower().replace(" ", "")
    out = {"open": {}, "now": {}}
    ml = odds.get("moneyline") or {}
    if ml:
        for ph, key in (("open", "open"), ("now", "close")):
            v = [american(((ml.get(s) or {}).get(key) or {}).get("odds")) for s in ("home", "draw", "away")]
            if all(v):
                out[ph]["1x2"] = v
    elif odds.get("homeTeamOdds"):
        v = [(odds.get(k) or {}).get("value") for k in ("homeTeamOdds", "drawOdds", "awayTeamOdds")]
        if all(isinstance(x, (int, float)) and x > 1 for x in v):
            out["now"]["1x2"] = [float(x) for x in v]
    tot = odds.get("total") or {}
    for ph, key in (("open", "open"), ("now", "close")):
        o, u = (tot.get("over") or {}).get(key) or {}, (tot.get("under") or {}).get(key) or {}
        L, po, pu = _line(o.get("line")), american(o.get("odds")), american(u.get("odds"))
        if L is not None and po and pu:
            out[ph]["ou"] = (L, po, pu)
    sp = odds.get("pointSpread") or {}
    for ph, key in (("open", "open"), ("now", "close")):
        h, a = (sp.get("home") or {}).get(key) or {}, (sp.get("away") or {}).get(key) or {}
        L, ph_, pa = _line(h.get("line")), american(h.get("odds")), american(a.get("odds"))
        if L is not None and ph_ and pa:
            out[ph]["ah"] = (L, ph_, pa)
    if not out["now"]:
        return book, None
    return book, out


def fetch(days=3, session=None, log=print, sleep=time.sleep):
    """Upcoming matches of the board's leagues with odds: list of dicts."""
    s = session or requests.Session()
    now = datetime.now(timezone.utc)
    rows = {}
    for lgname, code in CODES.items():
        for k in range(days + 1):
            d = (now + timedelta(days=k)).strftime("%Y%m%d")
            try:
                r = s.get(URL.format(lg=code), params={"dates": d, "limit": 300}, timeout=30)
                if r.status_code != 200:
                    continue
                js = r.json()
            except (requests.RequestException, ValueError):
                continue
            for ev in js.get("events") or []:
                comp = (ev.get("competitions") or [{}])[0]
                st = ((comp.get("status") or {}).get("type") or {})
                if st.get("state") not in (None, "pre"):
                    continue
                t = {c.get("homeAway"): c for c in comp.get("competitors") or []}
                if "home" not in t or "away" not in t:
                    continue
                try:
                    start = int(datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp())
                except (KeyError, ValueError):
                    continue
                book, pr = prices(next((o for o in comp.get("odds") or [] if o), None))
                rows[str(ev["id"])] = {"id": str(ev["id"]), "league": lgname, "code": code, "start": start,
                                       "home": (t["home"].get("team") or {}).get("displayName") or "?",
                                       "away": (t["away"].get("team") or {}).get("displayName") or "?",
                                       "book": book, "prices": pr}
            sleep(0.15)
    out = sorted(rows.values(), key=lambda x: x["start"])
    log(f"[espn] {len(out)} upcoming matches, {sum(1 for x in out if x['prices'])} with odds")
    return out


def record(data_dir, rows, now=None):
    """Append the current prices to the stored history; returns the store."""
    now = int(now or time.time())
    p = os.path.join(data_dir, "espn_odds.json")
    st = json.load(open(p)) if os.path.exists(p) else {}
    for r in rows:
        pr = r.get("prices")
        if not pr:
            continue
        e = st.setdefault(r["id"], {"first": now, "snaps": []})
        e.update({k: r[k] for k in ("league", "code", "start", "home", "away", "book")})
        if not e["snaps"] and pr.get("open"):
            e["snaps"].append([min(now, e["first"]) - 3600, pr["open"]])     # the book's opening price
        if not e["snaps"] or e["snaps"][-1][1] != pr["now"]:
            e["snaps"].append([now, pr["now"]])
    cut = now - 3 * 86400
    st = {k: v for k, v in st.items() if v.get("start", 0) > cut}
    os.makedirs(data_dir, exist_ok=True)
    json.dump(st, open(p, "w"), ensure_ascii=False, separators=(",", ":"))
    return st


def compact(e):
    """Stored snapshots -> the board's compact odds format {book: {"1x2": {...}, "ou": {...}, "ah": {...}}}.
    When the main line changes, the old line gets a [t, None] marker so it stops counting from then on."""
    d = {"1x2": {}, "ou": {}, "ah": {}}
    cur = {"ou": None, "ah": None}
    names = {"ou": ("over", "under"), "ah": ("home", "away")}
    for t, s in e.get("snaps") or []:
        if s.get("1x2"):
            for lab, v in zip(("home", "draw", "away"), s["1x2"]):
                d["1x2"].setdefault(lab, []).append([t, v])
        for kind in ("ou", "ah"):
            if not s.get(kind):
                continue
            L, x, y = s[kind]
            key = str(float(L))
            if cur[kind] is not None and cur[kind] != key:
                for lab in names[kind]:
                    d[kind][cur[kind]][lab].append([t, None])
            cur[kind] = key
            g = d[kind].setdefault(key, {})
            g.setdefault(names[kind][0], []).append([t, x])
            g.setdefault(names[kind][1], []).append([t, y])
    return {e.get("book") or "espn": d} if (d["1x2"] or d["ou"] or d["ah"]) else {}


def fixtures(store, hours, now=None, skip=None):
    """Pseudo OddsPapi fixtures (with their odds attached as '_comp') for the next `hours`."""
    now = now or time.time()
    out = []
    for k, e in store.items():
        if not (now < e.get("start", 0) < now + hours * 3600):
            continue
        if skip and skip(e):
            continue
        out.append({"fixtureId": f"espn{k}", "participant1Name": e["home"], "participant2Name": e["away"],
                    "startTime": datetime.fromtimestamp(e["start"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "_pm": e["league"], "categoryName": COUNTRY.get(e.get("code", "").split(".")[0], ""),
                    "tournamentName": e["league"], "_comp": compact(e)})
    return sorted(out, key=lambda f: f["startTime"])
