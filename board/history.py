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
         "lg": f.get("tournamentName") or str(f.get("tournamentId") or ""), "tid": str(f.get("tournamentId") or ""),
         "hs": result[0], "as": result[1], "v": ROW_V}
    def lt(kind):
        t = O.last_move(bd, kind, k - 60)
        return round((k - t) / 3600, 2) if t else None

    if "ou" in op and "ou" in cl:
        r["ou"] = {"ol": op["ou"]["line"], "cl": cl["ou"]["line"], "olam": op["ou"].get("lam"), "clam": cl["ou"].get("lam"),
                   "codds": cl["ou"]["odds"], "oodds": op["ou"]["odds"], "lt": lt("ou")}
    if "ah" in op and "ah" in cl:
        r["ah"] = {"ol": op["ah"]["line"], "cl": cl["ah"]["line"], "osup": op["ah"]["sup"], "csup": cl["ah"]["sup"],
                   "codds": cl["ah"]["odds"], "oodds": op["ah"]["odds"], "lt": lt("ah")}
    if "1x2" in op and "1x2" in cl:
        r["x2"] = {"op": op["1x2"]["p"], "cp": cl["1x2"]["p"], "codds": cl["1x2"]["odds"], "oodds": op["1x2"]["odds"]}
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


def _sim(name, names):
    """Share of the shorter name's words found in the other (0..1), best over ESPN's name variants."""
    from .stats import toks, _eq
    ta = toks(name)
    best = 0.0
    for n in names:
        tb = toks(n)
        if not ta or not tb:
            continue
        hit = sum(1 for x in ta if any(_eq(x, y) for y in tb))
        best = max(best, hit / max(len(ta), len(tb)))
    return best


def espn_row(teams, f):
    """The ESPN result of this fixture: same kick-off (+-36 h), and BOTH team names close at once.
    Matching each name on its own picked wrong teams (CF Montreal -> Montrose, Orlando City -> Orlando Pride)."""
    k = ts(f.get("startTime"))
    hn, an = f.get("participant1Name") or "", f.get("participant2Name") or ""
    if not k or not hn or not an:
        return None
    idx = getattr(teams, "_by_day", None)
    if idx is None:
        idx = {}
        for r in teams.res:
            idx.setdefault(r["t"] // 86400, []).append(r)
        teams._by_day = idx
    best, second = (0.0, None), 0.0
    for d in (k // 86400 - 1, k // 86400, k // 86400 + 1):
        for r in idx.get(d, []):
            if abs(r["t"] - k) > 36 * 3600:
                continue
            sh, sa = _sim(hn, r["home"]), _sim(an, r["away"])
            if min(sh, sa) < 0.5:
                continue
            sc = sh + sa
            if sc > best[0]:
                best, second = (sc, r), best[0]
            elif sc > second:
                second = sc
    if best[1] is None or best[0] - second < 0.25:       # nothing, or two results equally plausible
        return None
    return best[1]


def espn_score(teams, f):
    r = espn_row(teams, f)
    return (r["hs"], r["as"]) if r else None


def backfill(api, sid, cls, data_dir, teams, budget_s, log=print, window_days=7, allowed=None):
    """Spend up to `budget_s` seconds adding finished matches. Remembers which weeks were listed and which
    fixtures were tried (data/history/state.json), so successive runs walk back through the season."""
    d = os.path.join(data_dir, "history")
    os.makedirs(d, exist_ok=True)
    sp = os.path.join(d, "state.json")
    st = json.load(open(sp)) if os.path.exists(sp) else {"weeks": {}, "tried": []}
    tried = set(st["tried"])
    if st.get("v") != 2:                                   # results are now matched on both names at once: give
        have = {r.get("id") for r in load_rows(data_dir)}  # every fixture dropped for "no score" another try
        tried &= have
        st["v"] = 2
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
        # (no hasOdds filter here: finished matches report hasOdds=false once their markets close)
        queue += [f for f in fx if str(f.get("fixtureId")) not in tried
                  and (allowed is None or str(f.get("tournamentId")) in allowed)]
        if len(queue) > 400:
            break
        wk = a
    with open(rows_path, "a", encoding="utf-8") as fh:
        for f in queue:
            if time.time() - t0 > budget_s:
                break
            fid = str(f.get("fixtureId"))
            res = _score(f) or espn_score(teams, f)        # ESPN, both team names matched together
            if res is None and time.time() - (ts(f.get("startTime")) or 0) < 4 * 86400:
                continue                                   # result not in yet: try again next run
            tried.add(fid)
            if res is None:
                continue
            try:
                js = api.get("/v4/historical-odds", fixtureId=fid, bookmakers="pinnacle")
            except RuntimeError as e:
                if " 404" not in str(e) and "NOT_FOUND" not in str(e):
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


ROW_V = 2      # 2: built from full-time markets only (v1 rows mixed team totals into O/U and "2Up" into 1X2)


def retime(api, cls, data_dir, budget_s, log=print):
    """Rebuild rows saved by older versions (v1: wrong O/U and 1X2 markets, no line-move times) from a fresh
    copy of their odds (free endpoint), newest first, as many as the budget allows."""
    p = os.path.join(data_dir, "history", "rows.jsonl")
    if not os.path.exists(p) or budget_s <= 0:
        return 0
    rows, seen = [], set()
    for x in open(p, encoding="utf-8"):
        if x.strip():
            r = json.loads(x)
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
    todo = sorted([r for r in rows if r.get("v") != ROW_V], key=lambda r: -r["t"])
    t0, done, dropped = time.time(), 0, set()
    new = {}
    for r in todo:
        if time.time() - t0 > budget_s:
            break
        f = {"fixtureId": r["id"], "startTime": datetime.fromtimestamp(r["t"], timezone.utc).isoformat(),
             "participant1Name": r["home"], "participant2Name": r["away"], "tournamentName": r.get("lg"),
             "tournamentId": r.get("tid")}
        try:
            js = api.get("/v4/historical-odds", fixtureId=r["id"], bookmakers="pinnacle")
            nr = row_from(f, O.compact(js, cls, ["pinnacle"]), (r["hs"], r["as"]))
        except RuntimeError:
            nr = None
        if nr:
            new[r["id"]] = nr
        else:
            dropped.add(r["id"])
        done += 1
    if done:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in rows:
                if r["id"] in dropped:
                    continue
                fh.write(json.dumps(new.get(r["id"], r), ensure_ascii=False) + "\n")
        os.replace(tmp, p)
    log(f"[history] rebuilt {done} older matches ({len(dropped)} dropped); {len(todo) - done} still to rebuild")
    return done


def load_rows(data_dir):
    p = os.path.join(data_dir, "history", "rows.jsonl")
    if not os.path.exists(p):
        return []
    seen, out = set(), []
    for x in open(p, encoding="utf-8"):
        if x.strip():
            r = json.loads(x)
            if r["id"] not in seen and r.get("v") == ROW_V:
                seen.add(r["id"])
                out.append(r)
    return out


# ---------------------------------------------------------------- aggregate

def _bin(k, cap=3):
    return max(-cap, min(cap, k))


def group_stats(rows, kind):
    """{"open|close": {...}} for 'ou' (total line) or 'ah' (home handicap; k = steps, + = up / towards home).
    Grouped by the exact opening and closing line: 2 -> 2.25 and 3 -> 3.25 are different groups."""
    g = {}
    for r in rows:
        m = r.get(kind)
        if not m:
            continue
        goals, diff = r["hs"] + r["as"], r["hs"] - r["as"]
        if kind == "ou":
            k = steps(m["ol"], m["cl"])
            follow = (settle_over(goals, m["cl"], m["codds"][0]) if k > 0 else
                      settle_under(goals, m["cl"], m["codds"][1]) if k < 0 else None)
            fade = (settle_under(goals, m["cl"], m["codds"][1]) if k > 0 else
                    settle_over(goals, m["cl"], m["codds"][0]) if k < 0 else None)
            went = (goals > m["ol"]) if k > 0 else (goals < m["ol"]) if k < 0 else None
            x = {"k": k, "val": goals, "o": m.get("olam"), "c": m.get("clam"), "follow": follow, "fade": fade, "went": went}
        else:
            k = -steps(m["ol"], m["cl"])                   # home line more negative = moved towards home = +
            follow = (settle_home(diff, m["cl"], m["codds"][0]) if k > 0 else
                      settle_away(diff, m["cl"], m["codds"][1]) if k < 0 else None)
            fade = (settle_away(diff, m["cl"], m["codds"][1]) if k > 0 else
                    settle_home(diff, m["cl"], m["codds"][0]) if k < 0 else None)
            went = (diff + m["ol"] > 0) if k > 0 else (diff + m["ol"] < 0) if k < 0 else None
            x = {"k": k, "val": diff, "o": m.get("osup"), "c": m.get("csup"), "follow": follow, "fade": fade, "went": went}
        g.setdefault((m["ol"], m["cl"]), []).append(x)
    out = {}
    for (ol, cl), xs in sorted(g.items()):
        k = xs[0]["k"]
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
        out[f"{ol:g}|{cl:g}"] = {"from": ol, "to": cl, "k": k, "n": n, "actual": avg("val"), "open": avg("o"), "close": avg("c"),
                  "went": round(sum(went) / len(went), 3) if went else None,
                  "follow": round(sum(fol) / len(fol), 4) if fol else None, "follow_ci": ci(fol),
                  "fade": round(sum(fad) / len(fad), 4) if fad else None, "fade_ci": ci(fad)}
    return out


def fd_ah_rows(fdrows):
    """football-data matches (Pinnacle early-week vs closing Asian line, 2019+) in the board's row shape."""
    def sup(line, odds):
        if not odds:
            return None
        inv = [1 / x for x in odds]
        return round(-line + (inv[0] / sum(inv) - 0.5) * 3.8, 3)
    out = []
    for r in fdrows:
        if r.get("ah") is None or r.get("ahc") is None or not r.get("ahco"):
            continue
        out.append({"hs": r["hg"], "as": r["ag"], "t": r["d"],
                    "ah": {"ol": r["ah"], "cl": r["ahc"], "codds": r["ahco"], "osup": sup(r["ah"], r.get("aho")),
                           "csup": sup(r["ahc"], r["ahco"])}})
    return out


def aggregate(data_dir, tmap=None, fd=None):
    """All leagues together, plus each league on its own (the board's rows by tournament, and football-data's
    Asian lines for the leagues it covers)."""
    rows = load_rows(data_dir)
    out = {"n": len(rows), "since": min((r["t"] for r in rows), default=None),
           "ou": group_stats(rows, "ou"), "ah": group_stats(rows, "ah"), "leagues": {}, "fd": {}}
    by = {}
    for r in rows:
        lg = (tmap or {}).get(str(r.get("tid") or ""))
        if lg:
            by.setdefault(lg, []).append(r)
    for lg, rs in by.items():
        out["leagues"][lg] = {"n": len(rs), "ou": group_stats(rs, "ou"), "ah": group_stats(rs, "ah")}
    if fd is not None:
        allr = []
        for lg, frs in fd.rows.items():
            rs = fd_ah_rows(frs)
            if len(rs) >= 200:
                out["fd"][lg] = {"n": len(rs), "since": min(r["t"] for r in rs), "ah": group_stats(rs, "ah")}
                allr += rs
        if allr:
            out["fd"]["全部"] = {"n": len(allr), "since": min(r["t"] for r in allr), "ah": group_stats(allr, "ah")}
    return out


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
