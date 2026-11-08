"""Which soccer leagues trade the most on Polymarket, and which OddsPapi tournaments they are.

Ranking (refreshed weekly, cached in data/poly_leagues.json):
  Polymarket gamma: every soccer event (tag 'soccer') that is open or ended in the last `days` days;
  league = the event's series title (else its most specific tag); volume = sum of event volumes.
Mapping to OddsPapi (updated every run, remembered across runs):
  team names in each Polymarket league (from "Team A vs. Team B" titles) vs the teams in each OddsPapi tournament;
  a tournament belongs to the league that contains most of its teams.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from .stats import same_team, toks

GAMMA = "https://gamma-api.polymarket.com"
GENERIC_TAGS = {"soccer", "sports", "games", "football", "futbol", "all", "match", "matches"}


def _items(js):
    if isinstance(js, list):
        return js
    if isinstance(js, dict):
        for k in ("data", "events", "results", "items"):
            if isinstance(js.get(k), list):
                return js[k]
    return []


def _cursor(js):
    if isinstance(js, dict):
        for k in ("next_cursor", "nextCursor", "cursor"):
            if js.get(k):
                return js[k]
        pg = js.get("pagination")
        if isinstance(pg, dict) and pg.get("has_more", True):
            return pg.get("next_cursor") or pg.get("nextCursor")
    return None


def _date(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def league_of(ev):
    for s in ev.get("series") or []:
        t = (s or {}).get("title") or (s or {}).get("slug")
        if t:
            return str(t).strip()
    labels = [str(t.get("label") or t.get("slug") or "") for t in ev.get("tags") or [] if isinstance(t, dict)]
    labels = [x for x in labels if x and x.strip().lower() not in GENERIC_TAGS]
    return min(labels, key=len) if labels else None


def teams_of(ev):
    t = str(ev.get("title") or "")
    t = re.sub(r"\s*[-–(].*$", "", t)                     # "A vs. B - More Markets" / "(Nov 8)"
    parts = re.split(r"\s+vs\.?\s+|\s+v\s+|\s+@\s+", t, flags=re.I)
    return [p.strip() for p in parts if p.strip()] if len(parts) == 2 else []


def _events(s, tag_id, closed, since, max_pages, log):
    seen = set()
    off, cur = 0, None
    legacy = True
    for pg in range(max_pages):
        try:
            if legacy:
                r = s.get(f"{GAMMA}/events", params={"tag_id": tag_id, "closed": closed, "limit": 100, "offset": off,
                                                     "order": "endDate", "ascending": "false"}, timeout=20)
                if r.status_code == 422:
                    legacy = False
                    continue
            else:
                params = {"tag_id": tag_id, "closed": closed, "limit": 100}
                if cur:
                    params["after_cursor"] = cur
                r = s.get(f"{GAMMA}/events/keyset", params=params, timeout=20)
            r.raise_for_status()
            js = r.json()
        except (requests.RequestException, ValueError) as e:
            log(f"[poly] events page failed: {type(e).__name__}")
            return
        evs = _items(js)
        if not evs:
            return
        older = 0
        for e in evs:
            if str(e.get("id")) in seen:
                continue
            seen.add(str(e.get("id")))
            end = _date(e.get("endDate"))
            if closed == "true" and end and end < since:
                older += 1
                continue
            yield e
        if legacy:
            if closed == "true" and older == len(evs):
                return
            off += len(evs)
        else:
            cur = _cursor(js)
            if not cur:
                return


def rank(days=60, top=20, session=None, log=print, max_pages=300):
    s = session or requests.Session()
    s.headers["User-Agent"] = "football-board/1.0"
    r = s.get(f"{GAMMA}/tags/slug/soccer", timeout=20)
    r.raise_for_status()
    tag = str(r.json()["id"])
    since = datetime.now(timezone.utc) - timedelta(days=days)
    vol, teams, n = {}, {}, 0
    for closed in ("false", "true"):
        for e in _events(s, tag, closed, since, max_pages, log):
            lg = league_of(e)
            if not lg:
                continue
            try:
                v = float(e.get("volume") or 0)
            except (TypeError, ValueError):
                v = 0.0
            vol[lg] = vol.get(lg, 0.0) + v
            teams.setdefault(lg, set()).update(teams_of(e))
            n += 1
    ranked = sorted(vol.items(), key=lambda x: -x[1])
    out = [{"league": lg, "volume": round(v), "teams": sorted(teams.get(lg, []))} for lg, v in ranked
           if len(teams.get(lg, ())) >= 4][:top]          # leagues = competitions with real matches
    log(f"[poly] {n} soccer events; top leagues: " + ", ".join(f"{x['league']} (${x['volume']:,})" for x in out))
    return out


def assign(top, fixtures, tmap, log=print):
    """Map OddsPapi tournamentId -> Polymarket league by team overlap. Keeps earlier assignments."""
    by_t = {}
    for f in fixtures:
        tid = str(f.get("tournamentId") or "")
        if tid:
            by_t.setdefault(tid, set()).update([f.get("participant1Name") or "", f.get("participant2Name") or ""])
    pm_teams = {x["league"]: x["teams"] for x in top}
    new = 0
    for tid, names in by_t.items():
        names = {n for n in names if n and toks(n)}
        if tid in tmap or len(names) < 2:
            continue
        best, best_score = None, 0.0
        for lg, ts_ in pm_teams.items():
            hit = sum(1 for n in names if any(same_team(n, t) for t in ts_))
            score = hit / len(names)
            if hit >= 2 and score > best_score:
                best, best_score = lg, score
        if best and best_score >= 0.6:
            tmap[tid] = best
            new += 1
    log(f"[poly] {new} new OddsPapi tournaments matched; {len(tmap)} in total")
    return tmap


def load(data_dir, fixtures, refresh_days=7, log=print, session=None):
    """-> (top leagues list, {tournamentId: league}) or (None, None) if Polymarket could not be read."""
    path = os.path.join(data_dir, "poly_leagues.json")
    st = json.load(open(path)) if os.path.exists(path) else {}
    if not st.get("top") or time.time() - st.get("at", 0) > refresh_days * 86400:
        try:
            top = rank(session=session, log=log)
        except Exception as e:                       # noqa: BLE001
            log(f"[poly] ranking failed ({type(e).__name__}: {str(e)[:120]}); keeping the previous list")
            top = st.get("top")
        if not top:
            return None, None
        if [x["league"] for x in top] != [x["league"] for x in st.get("top") or []]:
            st["tmap"] = {}                          # new top list: remap tournaments
        st.update(at=time.time(), top=top)
    st["tmap"] = assign(st["top"], fixtures, st.get("tmap") or {}, log=log)
    os.makedirs(data_dir, exist_ok=True)
    json.dump(st, open(path, "w"), ensure_ascii=False)
    return st["top"], st["tmap"]
