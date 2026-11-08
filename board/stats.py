"""Team form from ESPN's free scoreboard (results of every league ESPN covers), cached per day in data/results.

Per team (last 10 matches, plus home-only / away-only): goals for / against, over 2.5 %, both-teams-score %,
clean sheets, form string. A simple expected total for a fixture combines both sides' attack and defence.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard"
# used when the 'all' scoreboard is not available
LEAGUES = ["eng.1", "eng.2", "eng.3", "eng.4", "eng.fa", "eng.league_cup", "esp.1", "esp.2", "esp.copa_del_rey",
           "ita.1", "ita.2", "ita.coppa_italia", "ger.1", "ger.2", "ger.dfb_pokal", "fra.1", "fra.2", "fra.coupe_de_france",
           "ned.1", "ned.2", "por.1", "bel.1", "sco.1", "tur.1", "gre.1", "aut.1", "sui.1", "den.1", "nor.1", "swe.1",
           "rus.1", "uefa.champions", "uefa.europa", "uefa.europa.conf", "usa.1", "mex.1", "bra.1", "arg.1", "col.1",
           "chi.1", "jpn.1", "kor.1", "chn.1", "aus.1", "sau.1", "conmebol.libertadores", "conmebol.sudamericana",
           "fifa.world", "fifa.worldq.uefa", "uefa.nations", "fifa.friendly"]


def _date(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_board(js, lg="all"):
    out = []
    leagues = {str(x.get("id")): x.get("name") or x.get("abbreviation") for x in js.get("leagues") or []}
    for ev in js.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        st = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
        if not st.get("completed"):
            continue
        t = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in t or "away" not in t:
            continue
        try:
            hs, as_ = int(t["home"].get("score")), int(t["away"].get("score"))
        except (TypeError, ValueError):
            continue
        d = _date(ev.get("date"))
        if not d:
            continue
        nm = lambda c: [(c.get("team") or {}).get(k, "") for k in ("displayName", "shortDisplayName", "name")]
        lgname = (ev.get("league") or {}).get("name") if isinstance(ev.get("league"), dict) else None
        out.append({"id": str(ev.get("id")), "t": int(d.timestamp()), "lg": lgname or leagues.get(str(ev.get("leagueId")))
                    or lg, "home": nm(t["home"]), "away": nm(t["away"]), "hs": hs, "as": as_})
    return out


def fetch_results(data_dir, days=150, recent=3, session=None, sleep=time.sleep, log=print):
    """One cached file per day (all leagues). Re-fetches the last `recent` days every run."""
    s = session or requests.Session()
    d = os.path.join(data_dir, "results")
    os.makedirs(d, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    mode = None
    fetched = 0
    for k in range(days, -1, -1):
        day = today - timedelta(days=k)
        path = os.path.join(d, f"{day:%Y%m%d}.json")
        if os.path.exists(path) and k > recent:
            continue
        rows = []
        if mode in (None, "all"):
            try:
                r = s.get(ESPN.format(lg="all"), params={"dates": f"{day:%Y%m%d}", "limit": 1000}, timeout=30)
                if r.status_code == 200 and (r.json().get("events") is not None):
                    rows = parse_board(r.json())
                    mode = "all"
                elif mode is None:
                    mode = "list"
            except (requests.RequestException, ValueError):
                if mode is None:
                    mode = "list"
        if mode == "list":
            for lg in LEAGUES:
                try:
                    r = s.get(ESPN.format(lg=lg), params={"dates": f"{day:%Y%m%d}", "limit": 500}, timeout=30)
                    if r.status_code == 200:
                        rows += [dict(x, lg=x["lg"] if x["lg"] != lg else lg) for x in parse_board(r.json(), lg)]
                except (requests.RequestException, ValueError):
                    pass
                sleep(0.15)
        json.dump(rows, open(path, "w"))
        fetched += 1
        sleep(0.2)
    log(f"[stats] results: {fetched} day(s) fetched from ESPN (mode {mode or 'cached'})")


def load_results(data_dir, days=150):
    d = os.path.join(data_dir, "results")
    if not os.path.isdir(d):
        return []
    cut = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
    rows, seen = [], set()
    for f in sorted(os.listdir(d)):
        if f[:8] < cut:
            continue
        for r in json.load(open(os.path.join(d, f))):
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
    return sorted(rows, key=lambda r: r["t"])


# ---------------------------------------------------------------- team names

STOP = {"fc", "cf", "afc", "sc", "ac", "the", "club", "de", "cd", "ud", "sd", "rc", "fk", "sk", "ssc", "as", "us",
        "calcio", "sv", "vfb", "vfl", "tsg", "bk", "if", "ik", "cp", "ca", "sl", "ec", "se", "cr", "1"}
ALIAS = {"man": "manchester", "utd": "united", "spurs": "tottenham", "wolves": "wolverhampton", "nottm": "nottingham",
         "munchen": "munich", "atleti": "atletico", "barca": "barcelona", "gladbach": "monchengladbach",
         "internazionale": "inter", "psg": "paris saint germain", "st": "saint"}
GENERIC = {"city", "united", "real", "sporting", "athletic", "atletico", "olympique", "racing", "deportivo", "dynamo",
           "inter", "manchester", "borussia", "town", "county", "rovers", "wanderers", "fc", "club", "saint", "san",
           "santa", "union", "young", "boys", "academy", "nacional", "independiente", "universidad", "women", "w", "ii",
           "u21", "u23", "u19", "b"}


def toks(nm):
    s = unicodedata.normalize("NFKD", str(nm)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    out = []
    for w in s.split():
        if w in STOP:
            continue
        out += ALIAS.get(w, w).split()
    return out


def _eq(x, y):
    return x == y or (len(x) >= 4 and len(y) >= 4 and (x.startswith(y) or y.startswith(x) or x[:5] == y[:5]))


def same_team(a, b):
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    # youth / women / reserve sides never match the first team
    flags = {"women", "w", "u21", "u23", "u19", "ii", "b", "reserves"}
    if (set(ta) & flags) != (set(tb) & flags):
        return False
    for x, y in ((ta, tb), (tb, ta)):
        if len(x) == 1 and len(y) >= 2 and x[0] == "".join(w[0] for w in y):
            return True
    shared = [x for x in ta if any(_eq(x, y) for y in tb)]
    if not shared:
        return False
    rest_a = [x for x in ta if x not in shared]
    rest_b = [y for y in tb if not any(_eq(x, y) for x in ta)]
    if rest_a and rest_b:
        return False
    return any(x not in GENERIC for x in shared) or not (rest_a or rest_b)


class Teams:
    def __init__(self, results):
        self.res = results
        self.names = {}
        for r in results:
            for side in ("home", "away"):
                for n in r[side]:
                    if n:
                        self.names.setdefault(n, set()).add(r[side][0])
        self.cache = {}
        self.index = {}                         # 4-letter token prefix -> names (so lookups stay fast)
        for n in self.names:
            for w in toks(n):
                self.index.setdefault(w[:4], set()).add(n)

    def find(self, name):
        """ESPN displayName for an OddsPapi team name, or None."""
        if name in self.cache:
            return self.cache[name]
        cands = set()
        for w in toks(name):
            cands |= self.index.get(w[:4], set())
        if len(toks(name)) == 1 and len(toks(name)[0]) <= 4:          # initials like PSG / PSV: check all
            cands = set(self.names)
        hits = {disp for n in cands if same_team(name, n) for disp in self.names[n]}
        best = None
        if len(hits) == 1:
            best = next(iter(hits))
        elif hits:   # several candidates: keep the one whose name is closest in length / tokens
            best = min(hits, key=lambda h: (abs(len(toks(h)) - len(toks(name))), len(h)))
        self.cache[name] = best
        return best

    def matches(self, disp, before):
        return [r for r in self.res if r["t"] < before and disp in (r["home"][0], r["away"][0])]


def team_stats(teams, name, before, n=10, venue=None):
    disp = teams.find(name)
    if not disp:
        return None
    ms = teams.matches(disp, before)
    if venue:
        ms = [m for m in ms if (m["home"][0] == disp) == (venue == "home")]
    ms = ms[-n:]
    if not ms:
        return {"team": disp, "n": 0}
    gf = ga = o25 = btts = cs = 0
    form, last = [], []
    for m in ms:
        home = m["home"][0] == disp
        f, a = (m["hs"], m["as"]) if home else (m["as"], m["hs"])
        gf += f
        ga += a
        o25 += (f + a) > 2.5
        btts += f > 0 and a > 0
        cs += a == 0
        form.append("W" if f > a else "D" if f == a else "L")
        opp = m["away"][0] if home else m["home"][0]
        last.append({"t": m["t"], "opp": opp, "ha": "主" if home else "客", "score": f"{f}-{a}"})
    k = len(ms)
    return {"team": disp, "n": k, "gf": round(gf / k, 2), "ga": round(ga / k, 2), "o25": round(o25 / k, 2),
            "btts": round(btts / k, 2), "cs": round(cs / k, 2), "form": "".join(form[-5:]), "last": last[-5:][::-1]}


def expected_total(h_home, a_away):
    """Simple goals expectation: home side's attack vs away side's defence and vice versa (venue splits)."""
    if not h_home or not a_away or not h_home.get("n") or not a_away.get("n"):
        return None
    if h_home["n"] < 3 or a_away["n"] < 3:
        return None
    return round((h_home["gf"] + a_away["ga"]) / 2 + (a_away["gf"] + h_home["ga"]) / 2, 2)
