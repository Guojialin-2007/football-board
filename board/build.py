"""Build the board: upcoming fixtures -> odds history per bookmaker -> movement summary + team form -> site/data.json

  python -m board.build                 # normal run (GitHub Actions runs this every 6 hours)
  python -m board.build --inspect       # prints what the API returns (markets recognised, a sample fixture)

Environment: ODDSPAPI_KEY (required), HOURS (look-ahead, default 36), MAX_FIXTURES (default 700),
BUDGET_MIN (stop fetching odds after this many minutes, default 200), BOOKS (default pinnacle,bet365,1xbet).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from . import detail as D
from . import history as H
from . import ideas as I
from . import leagues as LG
from . import polyleagues as P
from . import odds as O
from . import signals as S
from . import stats as T
from .papi import Papi, name, ts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site")


def meta(api, refresh_days=7, log=print):
    """Soccer sport id, recognised markets and tournament names - cached because they use the monthly allowance."""
    path = os.path.join(DATA, "meta.json")
    m = json.load(open(path)) if os.path.exists(path) else {}
    if m and time.time() - m.get("at", 0) < refresh_days * 86400 and m.get("markets"):
        return m
    sports = api.get("/v4/sports")
    sp = next((s for s in sports if name(s).strip().lower() in ("soccer", "football")), None)
    if not sp:
        raise RuntimeError("no soccer in /v4/sports")
    sid = sp.get("sportId") or sp.get("id")
    cls = O.classify(api.get("/v4/markets"), sid)
    tours = {}
    try:
        for t in api.get("/v4/tournaments", sportId=sid):
            tid = t.get("tournamentId") or t.get("id")
            tours[str(tid)] = {"name": name(t), "country": t.get("categoryName") or ""}
    except RuntimeError as e:
        log(f"[meta] tournaments not loaded: {e}")
    kinds = {}
    for v in cls.values():
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    log(f"[meta] soccer sportId={sid}; markets recognised {kinds}; {len(tours)} tournaments")
    if len(kinds) < 3:
        raw = [x for x in api.get("/v4/markets") if x.get("sportId") == sid and not x.get("playerProp")][:60]
        for x in raw:
            log(f"[meta]   market {x.get('marketId')}: {x.get('marketName')} | {x.get('marketType')} | "
                f"{x.get('period')} | h={x.get('handicap')} | {[o.get('outcomeName') for o in x.get('outcomes') or []]}")
    m = {"at": time.time(), "sid": sid, "markets": cls, "tours": tours}
    os.makedirs(DATA, exist_ok=True)
    json.dump(m, open(path, "w"))
    return m


def upcoming(api, sid, hours):
    now = datetime.now(timezone.utc)
    rows = api.get("/v4/fixtures", sportId=sid, **{"from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                  "to": (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    rows = rows if isinstance(rows, list) else (rows or {}).get("data") or []
    out = []
    for f in rows:
        st = ts(f.get("startTime"))
        if not st or st < now.timestamp() or f.get("hasOdds") is False:
            continue
        out.append(f)
    return sorted(out, key=lambda f: f.get("startTime", ""))


def odds_for(api, f, cls, books, log=print):
    fid = f.get("fixtureId")
    try:
        js = api.get("/v4/historical-odds", fixtureId=fid, bookmakers=",".join(books))
    except RuntimeError as e:
        if " 404" in str(e) or "NOT_FOUND" in str(e):
            return None                                   # no odds recorded for this match at all
        if "pinnacle" in books and len(books) > 1:
            log(f"[odds] {fid}: {str(e)[:120]} -> retry with pinnacle only")
            js = api.get("/v4/historical-odds", fixtureId=fid, bookmakers="pinnacle")
            books = ["pinnacle"]
        else:
            raise
    return O.compact(js, cls, books)


def card(f, comp, teams, tours, now, hist=None):
    start = ts(f.get("startTime"))
    tid = str(f.get("tournamentId") or "")
    tinfo = tours.get(tid, {})
    lg = f.get("_pm") or f.get("tournamentName") or tinfo.get("name") or "?"
    country = f.get("categoryName") or tinfo.get("country") or ""
    home, away = f.get("participant1Name") or "?", f.get("participant2Name") or "?"
    fx = {"id": str(f.get("fixtureId")), "start": start, "league": lg, "country": country, "home": home, "away": away,
          "books": {}}
    for bk, bd in (comp or {}).items():
        sm = O.summarize(bd, now, start)
        if sm:
            fx["books"][bk] = sm
    hs = {"all": T.team_stats(teams, home, start), "venue": T.team_stats(teams, home, start, n=8, venue="home")}
    as_ = {"all": T.team_stats(teams, away, start), "venue": T.team_stats(teams, away, start, n=8, venue="away")}
    fx["stats"] = {"home": hs, "away": as_}
    fx["exp_total"] = T.expected_total(hs["venue"], as_["venue"])
    fx["flags"] = S.flags(fx)
    fx["hist"] = H.lookup(hist, fx)
    for kind, label in (("ou", "大小球"), ("ah", "亚盘")):
        h = (fx["hist"] or {}).get(kind)
        if h and h["n"] >= 30 and h.get("fade") is not None:
            better = "反着买" if h["fade"] > h["follow"] else "跟着买"
            fx["flags"].append({"k": "hist", "lv": 1, "dir": kind,
                                "t": f"历史：{label}同样{'升' if h['k'] > 0 else '降'} {abs(h['k'])} 档的 {h['n']} 场，"
                                     f"跟着买回报 {h['follow'] * 100:+.1f}%，反着买 {h['fade'] * 100:+.1f}%（{better}更好，样本内）"})
    fx["score"] = sum(x["lv"] for x in fx["flags"])
    return fx


def run(inspect=False, log=print):
    t0 = time.time()
    os.makedirs(SITE, exist_ok=True)
    env = lambda k, d: os.environ.get(k) or d          # empty repository variables fall back to the default
    hours = float(env("HOURS", 48))
    max_fx = int(env("MAX_FIXTURES", 700))
    budget = float(env("BUDGET_MIN", 110)) * 60
    hist_budget = float(env("HIST_MIN", 60)) * 60
    books = [b.strip() for b in env("BOOKS", "pinnacle,bet365,1xbet").split(",") if b.strip()][:3]
    api = Papi()
    m = meta(api, log=log)
    T.fetch_results(DATA, days=300, log=log)
    teams = T.Teams(T.load_results(DATA, days=300))
    hist = H.aggregate(DATA)
    fxs = upcoming(api, m["sid"], hours)
    top, tmap = (None, None)
    mode = env("LEAGUES", "list")                       # list = the fixed list in board/leagues.py
    if env("ONLY_TOP", "1") == "0":
        mode = "all"
    if mode == "list" and m.get("tours"):
        tmap = LG.match(m["tours"], log=log)
        top = [{"league": n, "volume": None} for n, _, _ in LG.RULES]
    elif mode == "poly":
        top, tmap = P.load(DATA, fxs, log=log)
    if tmap:
        kept = [f for f in fxs if str(f.get("tournamentId")) in tmap]
        for f in kept:
            f["_pm"] = tmap[str(f.get("tournamentId"))]
        log(f"[board] {len(top)} chosen leagues: kept {len(kept)} of {len(fxs)} fixtures")
        fxs = kept
    log(f"[board] {len(fxs)} fixtures in the next {hours:.0f} h; fetching odds for up to {min(max_fx, len(fxs))}")
    if inspect and fxs:
        log("[inspect] first fixture: " + json.dumps(fxs[0], ensure_ascii=False)[:800])
    now = time.time()
    cards, no_odds = [], 0
    for i, f in enumerate(fxs[:max_fx]):
        comp = None
        if time.time() - t0 < budget:
            try:
                comp = odds_for(api, f, m["markets"], books, log)
            except RuntimeError as e:
                log(f"[odds] {f.get('fixtureId')}: {str(e)[:160]}")
            if inspect and i == 0:
                log(f"[inspect] compacted odds books={list((comp or {}).keys())} "
                    f"kinds={ {b: {k: len(v) for k, v in d.items()} for b, d in (comp or {}).items()} }")
        c = card(f, comp, teams, m.get("tours", {}), now, hist)
        if not c["books"]:
            no_odds += 1
        cards.append(c)
        if (i + 1) % 50 == 0:
            log(f"[board]   {i + 1}/{min(max_fx, len(fxs))} fixtures, {(time.time() - t0) / 60:.0f} min")
    matched = sum(1 for c in cards if (c["stats"]["home"]["all"] or {}).get("n"))
    # ---- detail: same-league history (同赔 / 同升降), head-to-head, judgement
    rows = H.load_rows(DATA)
    by_league = {}
    for r in rows:
        lg = (tmap or {}).get(str(r.get("tid") or ""))
        if lg:
            by_league.setdefault(lg, []).append(r)
    for c in cards:
        c["detail"] = D.build(c, teams, by_league.get(c["league"], []))
    # ---- ideas (思路): remember every fixture seen, settle ideas once the score is known
    seen_p = os.path.join(DATA, "seen.json")
    seen = json.load(open(seen_p)) if os.path.exists(seen_p) else {}
    for c in cards:
        seen[c["id"]] = {"id": c["id"], "start": c["start"], "home": c["home"], "away": c["away"], "league": c["league"]}
    seen = {k: v for k, v in seen.items() if v["start"] > time.time() - 14 * 86400}
    json.dump(seen, open(seen_p, "w"), ensure_ascii=False)
    by_id = {r["id"]: r for r in rows}

    def result_of(idea):
        r = by_id.get(idea["match"])
        if r:
            return r["hs"], r["as"]
        from datetime import datetime as _dt, timezone as _tz
        f = {"participant1Name": idea.get("home"), "participant2Name": idea.get("away"),
             "startTime": _dt.fromtimestamp(idea["start"], _tz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        return H.espn_score(teams, f)
    try:
        I.run({**seen, **{c["id"]: c for c in cards}}, result_of, DATA, SITE, log=log)
    except Exception as e:                 # noqa: BLE001  never lose the board because of the ideas job
        log(f"[ideas] stopped: {type(e).__name__}: {str(e)[:160]}")
    try:                                   # spare time: finished matches for the "what happened before" tables
        H.backfill(api, m["sid"], m["markets"], DATA, teams, hist_budget, log=log,
                   allowed=set(tmap) if tmap else None)
    except Exception as e:                 # noqa: BLE001  never lose the board because of the history job
        log(f"[history] backfill stopped: {type(e).__name__}: {str(e)[:160]}")
    agg = H.aggregate(DATA)
    json.dump(agg, open(os.path.join(SITE, "history.json"), "w"), ensure_ascii=False)
    out = {"generated": int(time.time()), "hours": hours, "books": books, "fixtures": cards,
           "repo": os.environ.get("GITHUB_REPOSITORY", ""),
           "leagues": [{"league": x["league"], "volume": x["volume"]} for x in (top or [])],
           "summary": {"fixtures": len(cards), "with_odds": len(cards) - no_odds, "with_stats": matched,
                       "flags": sum(1 for c in cards if c["flags"])}}
    json.dump(out, open(os.path.join(SITE, "data.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    log(f"[board] done in {(time.time() - t0) / 60:.1f} min: {out['summary']}; API calls {api.calls}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true")
    a = ap.parse_args()
    try:
        run(inspect=a.inspect)
    except RuntimeError as e:
        print(f"[board] FAILED: {e}", file=sys.stderr)
        sys.exit(1)
