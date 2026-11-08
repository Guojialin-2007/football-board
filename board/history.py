"""History of line moves and how the matches really ended: "when the line jumped like this before, what happened?"

Backfill (runs in the spare time of every update, a few hundred finished matches per run):
  finished fixtures (OddsPapi, weekly windows) -> Pinnacle odds history (free) -> opening and closing state
  -> final score (ESPN results, matched by team names and date) -> one row in data/history/rows.jsonl

Aggregate (site/history.json):
  over/under   by how many quarter-goal steps ("档") the main line moved, open -> close
  handicap     by how many steps the home side's line moved
  For each group: matches, average goals (or goal difference) vs what the open and the close implied,
  how often the result went the way the line moved, and the return per $1 of
    "follow"  betting the side the line moved to, at the closing price
    "fade"    betting against the move (the "it's a trap" idea), at the closing price
  using Asian settlement (quarter lines settle half/half, whole lines push).
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

from . import odds as O
from . import stats as T
from .papi import ts

START = datetime(2026, 1, 15, tzinfo=timezone.utc)      # OddsPapi history starts around mid January 2026
AH_NAMES = {0: "平手", 0.25: "平半", 0.5: "半球", 0.75: "半一", 1: "一球", 1.25: "一球/球半", 1.5: "球半",
            1.75: "球半/两球", 2: "两球", 2.25: "两球/两球半", 2.5: "两球半", 2.75: "两球半/三球", 3: "三球"}


def ah_name(line):
    """Home handicap line -> Chinese name ("主让半球", "主受让平半")."""
    a = abs(line)
    nm = AH_NAMES.get(a, f"{a:g}球")
    if line == 0:
        return "平手"
    return ("主让" if line < 0 else "主受让") + nm


def steps(a, b):
    return int(round((b - a) / 0.25))


# ---------------------------------------------------------------- Asian settlement

def _half_lines(line):
    """A quarter line splits into two half-stakes: 2.75 -> (2.5, 3.0)."""
    frac = round((line * 4) % 2)
    if frac == 1:
        return (line - 0.25, line + 0.25)
    return (line,)


def settle_over(goals, line, odds):
    """Return per $1 on 'over line' at decimal odds (win -> odds-1, push -> 0, lose -> -1), averaged over halves."""
    res = []
    for L in _half_lines(line):
        res.append(odds - 1 if goals > L else (0.0 if goals == L else -1.0))
    return sum(res) / len(res)


def settle_under(goals, line, odds):
    res = []
    for L in _half_lines(line):
        res.append(odds - 1 if goals < L else (0.0 if goals == L else -1.0))
    return sum(res) / len(res)


def settle_home(diff, line, odds):
    """Home side with handicap `line` (negative = gives goals). diff = home goals - away goals."""
    res = []
    for L in _half_lines(line):
        x = diff + L
        res.append(odds - 1 if x > 0 else (0.0 if x == 0 else -1.0))
    return sum(res) / len(res)


def settle_away(diff, line, odds):
    res = []
    for L in _half_lines(line):
        x = -(diff + L)
        res.append(odds - 1 if x > 0 else (0.0 if x == 0 else -1.0))
    return sum(res) / len(res)


# ---------------------------------------------------------------- backfill

def _close_state(bd, kickoff):
    return O.state_at(bd, kickoff - 60)


def _open_state(bd, kickoff):
    st = {}
    for kind in ("1x2", "ou", "ah"):
        groups = [bd.get("1x2") or {}] if kind == "1x2" else list((bd.get(kind) or {}).values())
        for t in O._times(groups):
            if t >= kickoff:
                break
            s = O.state_at(bd, t).get(kind)
            if s:
                st[kind] = dict(s, t=t)
                break
    return st


def row_from(f, comp, result):
    bd = (comp or {}).get("pinnacle")
    if not bd:
        return None
    k = ts(f.get("startTime"))
    op, cl = _open_state(bd, k), _close_state(bd, k)
    r = {"id": str(f.get("fixtureId")), "t": k, "home": f.get("participant1Name"), "away": f.get("participant2Name"),
         "lg": f.get("tournamentName") or str(f.get("tournamentId") or ""), "hs": result[0], "as": result[1]}
    if "ou" in op and "ou" in cl:
        r["ou"] = {"ol": op["ou"]["line"], "cl": cl["ou"]["line"], "olam": op["ou"].get("lam"), "clam": cl["ou"].get("lam"),
                   "codds": cl["ou"]["odds"], "oodds": op["ou"]["odds"]}
    if "ah" in op and "ah" in cl:
        r["ah"] = {"ol": op["ah"]["line"], "cl": cl["ah"]["line"], "osup": op["ah"]["sup"], "csup": cl["ah"]["sup"],
                   "codds": cl["ah"]["odds"], "oodds": op["ah"]["odds"]}
    if "1x2" in op and "1x2" in cl:
        r["x2"] = {"op": op["1x2"]["p"], "cp": cl["1x2"]["p"], "codds": cl["1x2"]["odds"]}
    return r if ("ou" in r or "ah" in r) else None


def _score(f):
    """Final score from the OddsPapi fixture itself, if it carries one."""
    for a, b in (("participant1Score", "participant2Score"), ("homeScore", "awayScore"), ("score1", "score2")):
        if f.get(a) is not None and f.get(b) is not None:
            try:
                return int(f[a]), int(f[b])
            except (TypeError, ValueError):
                pass
    sc = f.get("scores") or f.get("score") or f.get("result")
    if isinstance(sc, dict):
        for a, b in (("participant1", "participant2"), ("home", "away"), ("1", "2")):
            try:
                return int(sc[a]), int(sc[b])
            except (KeyError, TypeError, ValueError):
                pass
    return None


def espn_score(teams, f):
    k = ts(f.get("startTime"))
    h, a = teams.find(f.get("participant1Name") or ""), teams.find(f.get("participant2Name") or "")
    if not h or not a or not k:
        return None
    for r in teams.res:
        if abs(r["t"] - k) <= 36 * 3600 and r["home"][0] == h and r["away"][0] == a:
            return r["hs"], r["as"]
    return None


def backfill(api, sid, cls, data_dir, teams, budget_s, log=print, window_days=7):
    """Spend up to `budget_s` seconds adding finished matches. Remembers which weeks were listed and which
    fixtures were tried (data/history/state.json), so successive runs walk back through the season."""
    d = os.path.join(data_dir, "history")
    os.makedirs(d, exist_ok=True)
    sp = os.path.join(d, "state.json")
    st = json.load(open(sp)) if os.path.exists(sp) else {"weeks": {}, "tried": []}
    tried = set(st["tried"])
    rows_path = os.path.join(d, "rows.jsonl")
    t0 = time.time()
    added = listed = 0
    end = datetime.now(timezone.utc) - timedelta(days=1)
    wk = end
    queue = []
    while wk > START and time.time() - t0 < budget_s:
        a = wk - timedelta(days=window_days)
        key = a.strftime("%Y-%m-%d")
        recent = (end - wk).days < 14                    # recent weeks: re-list once a day, results arrive late
        info = st["weeks"].get(key)
        stale = isinstance(info, dict) and recent and time.time() - info.get("at", 0) > 20 * 3600
        if info is None or stale:
            try:
                fx = api.get("/v4/fixtures", sportId=sid, statusId=2,
                             **{"from": a.strftime("%Y-%m-%dT00:00:00Z"), "to": wk.strftime("%Y-%m-%dT00:00:00Z")})
                fx = fx if isinstance(fx, list) else (fx or {}).get("data") or []
                listed += 1
            except RuntimeError as e:
                log(f"[history] fixtures {key}: {str(e)[:120]}")
                break
            json.dump(fx, open(os.path.join(d, f"fx_{key}.json"), "w"))
            st["weeks"][key] = {"n": len(fx), "at": time.time()}
        else:
            p = os.path.join(d, f"fx_{key}.json")
            fx = json.load(open(p)) if os.path.exists(p) else []
        queue += [f for f in fx if str(f.get("fixtureId")) not in tried]
        if len(queue) > 400:
            break
        wk = a
    with open(rows_path, "a", encoding="utf-8") as fh:
        for f in queue:
            if time.time() - t0 > budget_s:
                break
            fid = str(f.get("fixtureId"))
            res = _score(f) or espn_score(teams, f)
            if res is None and time.time() - (ts(f.get("startTime")) or 0) < 4 * 86400:
                continue                                   # result not in yet: try again next run
            tried.add(fid)
            if res is None:
                continue
            try:
                js = api.get("/v4/historical-odds", fixtureId=fid, bookmakers="pinnacle")
            except RuntimeError as e:
                log(f"[history] odds {fid}: {str(e)[:100]}")
                continue
            r = row_from(f, O.compact(js, cls, ["pinnacle"]), res)
            if r:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                added += 1
    st["tried"] = sorted(tried)
    json.dump(st, open(sp, "w"))
    log(f"[history] {listed} week lists fetched, {added} finished matches added in {(time.time() - t0) / 60:.0f} min")
    return added


def load_rows(data_dir):
    p = os.path.join(data_dir, "history", "rows.jsonl")
    if not os.path.exists(p):
        return []
    seen, out = set(), []
    for x in open(p, encoding="utf-8"):
        if x.strip():
            r = json.loads(x)
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(r)
    return out


# ---------------------------------------------------------------- aggregate

def _bin(k, cap=3):
    return max(-cap, min(cap, k))


def group_stats(rows, kind):
    """{step: {...}} for 'ou' (line steps of the total) or 'ah' (steps of the home handicap, + = towards home)."""
    g = {}
    for r in rows:
        m = r.get(kind)
        if not m:
            continue
        goals, diff = r["hs"] + r["as"], r["hs"] - r["as"]
        if kind == "ou":
            k = _bin(steps(m["ol"], m["cl"]))
            follow = (settle_over(goals, m["cl"], m["codds"][0]) if k > 0 else
                      settle_under(goals, m["cl"], m["codds"][1]) if k < 0 else None)
            fade = (settle_under(goals, m["cl"], m["codds"][1]) if k > 0 else
                    settle_over(goals, m["cl"], m["codds"][0]) if k < 0 else None)
            went = (goals > m["ol"]) if k > 0 else (goals < m["ol"]) if k < 0 else None
            x = {"k": k, "val": goals, "o": m.get("olam"), "c": m.get("clam"), "follow": follow, "fade": fade, "went": went}
        else:
            k = _bin(-steps(m["ol"], m["cl"]))             # home line more negative = moved towards home = +
            follow = (settle_home(diff, m["cl"], m["codds"][0]) if k > 0 else
                      settle_away(diff, m["cl"], m["codds"][1]) if k < 0 else None)
            fade = (settle_away(diff, m["cl"], m["codds"][1]) if k > 0 else
                    settle_home(diff, m["cl"], m["codds"][0]) if k < 0 else None)
            went = (diff + m["ol"] > 0) if k > 0 else (diff + m["ol"] < 0) if k < 0 else None
            x = {"k": k, "val": diff, "o": m.get("osup"), "c": m.get("csup"), "follow": follow, "fade": fade, "went": went}
        g.setdefault(k, []).append(x)
    out = {}
    for k, xs in sorted(g.items()):
        n = len(xs)
        def avg(key):
            v = [x[key] for x in xs if x[key] is not None]
            return round(sum(v) / len(v), 3) if v else None
        fol = [x["follow"] for x in xs if x["follow"] is not None]
        fad = [x["fade"] for x in xs if x["fade"] is not None]
        went = [x["went"] for x in xs if x["went"] is not None]
        def ci(v):
            if len(v) < 2:
                return None
            mu = sum(v) / len(v)
            sd = (sum((y - mu) ** 2 for y in v) / (len(v) - 1)) ** 0.5
            return [round(mu - 1.96 * sd / len(v) ** 0.5, 3), round(mu + 1.96 * sd / len(v) ** 0.5, 3)]
        out[k] = {"n": n, "actual": avg("val"), "open": avg("o"), "close": avg("c"),
                  "went": round(sum(went) / len(went), 3) if went else None,
                  "follow": round(sum(fol) / len(fol), 4) if fol else None, "follow_ci": ci(fol),
                  "fade": round(sum(fad) / len(fad), 4) if fad else None, "fade_ci": ci(fad)}
    return out


def aggregate(data_dir):
    rows = load_rows(data_dir)
    return {"n": len(rows), "since": min((r["t"] for r in rows), default=None),
            "ou": group_stats(rows, "ou"), "ah": group_stats(rows, "ah")}


def lookup(agg, fx):
    """The historical group that matches this fixture's move so far."""
    ref = (fx.get("books") or {}).get("pinnacle")
    if not ref or not agg or not agg.get("n"):
        return None
    out = {}
    if ref.get("ou"):
        k = _bin(steps(ref["ou"]["open"]["line"], ref["ou"]["now"]["line"]))
        if k and k in agg["ou"]:
            out["ou"] = dict(agg["ou"][k], k=k)
    if ref.get("ah"):
        k = _bin(-steps(ref["ah"]["open"]["line"], ref["ah"]["now"]["line"]))
        if k and k in agg["ah"]:
            out["ah"] = dict(agg["ah"][k], k=k)
    return out or None
