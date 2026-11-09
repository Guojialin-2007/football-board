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
from . import fd as FDM
from . import history as H
from . import ideas as I
from . import learn as L
from . import rules as RL
from . import track as TR
from . import leagues as LG
from . import polyleagues as P
from . import odds as O
from . import signals as S
from . import stats as T
from .papi import Papi, name, ts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site")


META_V = 2          # 2: team totals and "2Up" results no longer mixed into O/U and 1X2


def meta(api, refresh_days=7, log=print):
    """Soccer sport id, recognised markets and tournament names - cached because they use the monthly allowance."""
    path = os.path.join(DATA, "meta.json")
    m = json.load(open(path)) if os.path.exists(path) else {}
    if m and m.get("v") == META_V and time.time() - m.get("at", 0) < refresh_days * 86400 and m.get("markets"):
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
    m = {"v": META_V, "at": time.time(), "sid": sid, "markets": cls, "tours": tours}
    os.makedirs(DATA, exist_ok=True)
    json.dump(m, open(path, "w"))
    return m


def upcoming_cached(api, sid, hours, every_h, log=print):
    """/v4/fixtures counts against the monthly allowance, so fetch it every `every_h` hours (with that much extra
    look-ahead) and reuse the cached list in between. Returns (fixtures, fetched_now)."""
    p = os.path.join(DATA, "fixtures.json")
    c = json.load(open(p)) if os.path.exists(p) else {}
    now = time.time()
    if c.get("at") and now - c["at"] < every_h * 3600 - 900 and c.get("hours", 0) >= hours + every_h - 1:
        rows = [f for f in c["rows"] if (ts(f.get("startTime")) or 0) > now and (ts(f.get("startTime")) or 0) < now + hours * 3600]
        log(f"[board] fixture list from cache ({(now - c['at']) / 3600:.1f} h old)")
        return rows, False
    rows = upcoming(api, sid, hours + every_h)
    os.makedirs(DATA, exist_ok=True)
    json.dump({"at": now, "hours": hours + every_h, "rows": rows}, open(p, "w"), ensure_ascii=False)
    return [f for f in rows if (ts(f.get("startTime")) or 0) < now + hours * 3600], True


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
    comp = O.compact(js, cls, books)
    extra = [b for b in (os.environ.get("EXTRA_BOOKS") or "betano,dafabet,cloudbet").split(",") if b.strip()][:3]
    if comp and extra:                                   # more soft books = more chances of a price above Pinnacle's fair
        try:
            comp.update(O.compact(api.get("/v4/historical-odds", fixtureId=fid, bookmakers=",".join(extra)), cls, extra))
        except RuntimeError as e:
            if " 404" not in str(e) and "NOT_FOUND" not in str(e):
                log(f"[odds] {fid} extra books: {str(e)[:100]}")
    return comp


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
    fx["score"] = sum(x["lv"] for x in fx["flags"])
    return fx


def _ret(name, b):
    if b.get("follow") is None:
        return None
    ci = b.get("follow_ci")
    sig = "，区间跨过 0，看不出真差别" if ci and ci[0] < 0 < ci[1] else ""
    return f"{name} {b['n']} 场：跟着买每注 {b['follow'] * 100:+.1f}%，反着买 {b['fade'] * 100:+.1f}%{sig}"


def move_flags(c):
    """Flags from the exact same opening -> closing line: same league first, then the same move for each team."""
    sm = (c.get("detail") or {}).get("same_move") or {}
    out = []
    for kind in ("ou", "ah"):
        b = sm.get(kind)
        if not b or b["from"] == b["to"]:
            continue
        k = H.steps(b["from"], b["to"])
        if kind == "ou":
            up = k > 0
            mv = f"大小球 {b['from']:g} → {b['to']:g}"
            fol = f"买{'大' if up else '小'} {b['to']:g}"
            fad = f"买{'小' if up else '大'} {b['to']:g}"
        else:
            up = -k > 0
            mv = f"亚盘 {H.ah_name(b['from'])} → {H.ah_name(b['to'])}"
            fol, fad = ((f"买主队（{H.ah_name(b['to'])}）", "买客队") if up else ("买客队", f"买主队（{H.ah_name(b['to'])}）"))
        parts = [x for x in (_ret(f"同联赛（{c['league']}）", b) if b.get("n") else None,
                             *[_ret(f"{b[key]['team']}（{'主场' if key == 'home_team' else '客场'}）", b[key])
                               for key in ("home_team", "away_team") if (b.get(key) or {}).get("n")]) if x]
        if not parts:
            parts = ["同联赛、同球队还没有一模一样的变盘样本"]
        out.append({"k": "hist", "lv": 1 if (b.get("n") or 0) >= 30 else 0, "dir": kind,
                    "t": f"历史：{mv}（开盘→现在，一模一样的盘口）——跟着买 = {fol}，反着买 = {fad}，收盘赔率。"
                         + "；".join(parts)})
    return out


def run(inspect=False, log=print):
    t0 = time.time()
    os.makedirs(SITE, exist_ok=True)
    env = lambda k, d: os.environ.get(k) or d          # empty repository variables fall back to the default
    hours = float(env("HOURS", 48))
    max_fx = int(env("MAX_FIXTURES", 700))
    budget = float(env("BUDGET_MIN", 110)) * 60
    hist_budget = float(env("HIST_MIN", 35)) * 60
    books = [b.strip() for b in env("BOOKS", "pinnacle,bet365,1xbet").split(",") if b.strip()][:3]
    api = Papi()
    m = meta(api, log=log)
    T.fetch_results(DATA, days=300, log=log)
    teams = T.Teams(T.load_results(DATA, days=300))
    hist = None
    fxs, full = upcoming_cached(api, m["sid"], hours, float(env("FIXTURES_EVERY_H", 6)), log=log)
    if os.environ.get("FULL") == "1":
        full = True
    log(f"[board] {'full run (fixture list + history)' if full else 'quick run (odds only)'}")
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
        r["lgz"] = lg
        if lg:
            by_league.setdefault(lg, []).append(r)
    fdx = None
    try:                                   # football-data.co.uk: 20 seasons of results + odds
        FDM.fetch(DATA, [n for n, _, _ in LG.RULES], log=log, budget_s=float(env("FD_MIN", 12)) * 60)
        fdx = FDM.FD(DATA)
        log(f"[fd] {fdx.n()} matches loaded for {len(fdx.rows)} leagues")
    except Exception as e:                 # noqa: BLE001
        log(f"[fd] skipped: {type(e).__name__}: {str(e)[:160]}")
    lib = None
    try:                                   # 规律库: classic line-reading rules, backtested on football-data
        if fdx is not None:
            lib = RL.backtest(fdx)
            json.dump({"rules": lib["rules"], "base": lib["base"]}, open(os.path.join(SITE, "rules.json"), "w"),
                      ensure_ascii=False)
            log(f"[rules] {sum(r['verdict'] != '没有优势' for r in lib['rules'].values())} of {len(lib['rules'])} rules "
                f"show something")
    except Exception as e:                 # noqa: BLE001
        log(f"[rules] skipped: {type(e).__name__}: {str(e)[:160]}")
    for c in cards:
        c["detail"] = D.build(c, teams, by_league.get(c["league"], []), fdx, all_rows=rows)
        ref = (c.get("books") or {}).get("pinnacle")
        c["detail"]["rules"] = RL.for_match(ref, lib) if lib else []
        c["flags"] += move_flags(c)
        c["score"] = sum(x["lv"] for x in c["flags"])
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
    if not full:                           # quick hourly run: odds and details only, history waits for the full run
        hist_budget = 0.0
    try:                                   # learn from right ideas, make 系统思路 for matching matches
        L.run(cards, rows, result_of, DATA, SITE, log=log)
    except Exception as e:                 # noqa: BLE001
        log(f"[learn] stopped: {type(e).__name__}: {str(e)[:160]}")
    try:                                   # 信号战绩: record every signal before kickoff, settle finished matches
        TR.record(cards, DATA)
        st_tr = TR.settle_all(DATA, result_of, log=log)
        TR.attach(cards, TR.summary(st_tr, rows, SITE))
    except Exception as e:                 # noqa: BLE001
        log(f"[track] stopped: {type(e).__name__}: {str(e)[:160]}")
    t_rt = time.time()
    try:                                   # older history rows: add when the line last moved
        H.retime(api, m["markets"], DATA, float(env("RETIME_MIN", 15)) * 60 if full else 0.0, log=log)
    except Exception as e:                 # noqa: BLE001
        log(f"[history] retime stopped: {type(e).__name__}: {str(e)[:160]}")
    hist_budget = max(0.0, hist_budget - (time.time() - t_rt))
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
