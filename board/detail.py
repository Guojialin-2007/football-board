"""Per-match detail: head-to-head, "same odds" and "same line move" history, and a judgement with confidence.

Sources: football-data.co.uk (20 seasons, 17 of the 20 chosen leagues) first; for the rest the board's own
history (OddsPapi, since Jan 2026) and ESPN (about a year).
  交手      every meeting of the two teams in those 20 seasons (any league in the data)
  同赔(本队) the home team at home (and the away team away) when its opening 1X2 odds were close to today's
  同赔(联赛) any match of the same league with close opening odds
  同升降    same league, same opening -> closing handicap line (e.g. 主让半球 -> 主让半一)
  判断      always shown: market probability, what the history did (95% interval), and a blend
            est = (40 * market + n * history) / (40 + n); confidence by sample size: <30 低, <150 中, else 高
"""

from __future__ import annotations

import math

from . import history as H

K_SHRINK = 40


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / d
    return [round(c - h, 3), round(c + h, 3)]


def conf(n):
    return "低" if n < 30 else "中" if n < 150 else "高"


def _close(a, b, tol):
    return a and b and all(abs(a[i] - b[i]) <= tol * max(1.0, b[i] / 3) for i in range(3))


def _row_fd(r, flip=False):
    out = {"t": r["d"], "home": r["h"], "away": r["a"], "score": f"{r['hg']}-{r['ag']}"}
    if r.get("o") or r.get("c"):
        out["odds"] = r.get("o") or r.get("c")
    if r.get("ah") is not None:
        out["ah"] = [r["ah"], r.get("ahc", r["ah"])]
    return out


def _row_pp(r):
    out = {"t": r["t"], "home": r["home"], "away": r["away"], "score": f"{r['hs']}-{r['as']}"}
    if r.get("x2", {}).get("oodds"):
        out["odds"] = [round(x, 2) for x in r["x2"]["oodds"]]
    if r.get("ah"):
        out["ah"] = [r["ah"]["ol"], r["ah"]["cl"]]
    if r.get("ou"):
        out["ou"] = [r["ou"]["ol"], r["ou"]["cl"]]
    return out


def _summ(rs, home_name=None):
    """rs: list of (home, away, hg, ag) -> W/D/L from the first team's point of view (or the home side)."""
    n = len(rs)
    if not n:
        return {"n": 0}
    w = d = 0
    goals = []
    for h, a, hg, ag in rs:
        me_home = home_name is None or h == home_name
        f, g = (hg, ag) if me_home else (ag, hg)
        w += f > g
        d += f == g
        goals.append(hg + ag)
    return {"n": n, "w": w, "d": d, "l": n - w - d, "avg_goals": round(sum(goals) / n, 2),
            "o25": round(sum(x > 2.5 for x in goals) / n, 3), "btts": None}


def h2h(fd, teams, fx, limit=20):
    if fd is not None:
        a, b = fd.team(fx["home"], fx["league"]), fd.team(fx["away"], fx["league"])
        if a and b and a != b:
            ms = [r for r in fd.all_rows() if {r["h"], r["a"]} == {a, b} and r["d"] < fx["start"]]
            ms.sort(key=lambda r: -r["d"])
            if ms:
                s = _summ([(r["h"], r["a"], r["hg"], r["ag"]) for r in ms], a)
                return dict(s, src="football-data（近 20 年）", years=round((ms[0]["d"] - ms[-1]["d"]) / 31557600, 1),
                            list=[_row_fd(r) for r in ms[:limit]])
    ha, hb = teams.find(fx["home"]), teams.find(fx["away"])
    if not ha or not hb:
        return None
    ms = [r for r in teams.res if r["t"] < fx["start"] and {r["home"][0], r["away"][0]} == {ha, hb}][::-1]
    if not ms:
        return {"n": 0, "src": "ESPN（近一年）"}
    s = _summ([(r["home"][0], r["away"][0], r["hs"], r["as"]) for r in ms], ha)
    return dict(s, src="ESPN（近一年）", list=[{"t": r["t"], "home": r["home"][0], "away": r["away"][0],
                                                "score": f"{r['hs']}-{r['as']}"} for r in ms[:limit]])


def _odds_now(ref):
    x = (ref or {}).get("1x2")
    return (x["open"]["odds"], x["now"]["odds"]) if x else (None, None)


def same_odds(fd, league_rows, fx, ref, tol=0.08, limit=20):
    o_open, o_now = _odds_now(ref)
    if not o_open:
        return None
    out = {"odds": [round(v, 2) for v in o_open]}
    rows = (fd.rows.get(fx["league"]) if fd is not None else None) or []
    if rows:
        key = "o" if any(r.get("o") for r in rows[-50:]) else "c"       # extra leagues only have closing odds
        ref_odds = o_open if key == "o" else o_now
        before = [r for r in rows if r["d"] < fx["start"] and r.get(key)]
        lg = [r for r in before if _close(r[key], ref_odds, tol)]
        out["league"] = dict(_summ([(r["h"], r["a"], r["hg"], r["ag"]) for r in lg]), src="football-data（近 20 年）",
                             basis="开盘" if key == "o" else "收盘", list=[_row_fd(r) for r in sorted(lg, key=lambda r: -r["d"])[:limit]])
        a, b = fd.team(fx["home"], fx["league"]), fd.team(fx["away"], fx["league"])
        if a:
            th = [r for r in before if r["h"] == a and abs(r[key][0] - ref_odds[0]) <= tol * max(1.0, ref_odds[0] / 3)]
            out["home_team"] = dict(_summ([(r["h"], r["a"], r["hg"], r["ag"]) for r in th], a), team=a,
                                    list=[_row_fd(r) for r in sorted(th, key=lambda r: -r["d"])[:limit]])
        if b:
            ta = [r for r in before if r["a"] == b and abs(r[key][2] - ref_odds[2]) <= tol * max(1.0, ref_odds[2] / 3)]
            out["away_team"] = dict(_summ([(r["h"], r["a"], r["hg"], r["ag"]) for r in ta], b), team=b,
                                    list=[_row_fd(r) for r in sorted(ta, key=lambda r: -r["d"])[:limit]])
        return out
    sim = [r for r in league_rows if r.get("x2", {}).get("oodds") and _close(r["x2"]["oodds"], o_open, tol)]
    out["league"] = dict(_summ([(r["home"], r["away"], r["hs"], r["as"]) for r in sim]), src="本站历史（2026 起）",
                         basis="开盘", list=[_row_pp(r) for r in sorted(sim, key=lambda r: -r["t"])[:limit]])
    return out


def _ah_block(sim_rows, ol, cl, get, limit, src):
    """sim_rows: rows with the same move; get(r) -> (goal diff, closing line, closing odds [home, away])"""
    res = {"from": ol, "to": cl, "n": len(sim_rows), "src": src}
    if not sim_rows:
        return res
    sc = [H.settle_home(get(r)[0], cl, 2.0) for r in sim_rows]
    res.update(home_cover=round(sum(x > 0 for x in sc) / len(sc), 3), away_cover=round(sum(x < 0 for x in sc) / len(sc), 3),
               push=round(sum(x == 0 for x in sc) / len(sc), 3), home_k=sum(x > 0 for x in sc),
               away_k=sum(x < 0 for x in sc), follow=None, fade=None)
    k = -H.steps(ol, cl)
    priced = [r for r in sim_rows if get(r)[2]]
    if k and priced:
        fol = [(H.settle_home if k > 0 else H.settle_away)(get(r)[0], get(r)[1], get(r)[2][0 if k > 0 else 1]) for r in priced]
        fad = [(H.settle_away if k > 0 else H.settle_home)(get(r)[0], get(r)[1], get(r)[2][1 if k > 0 else 0]) for r in priced]
        res.update(follow=round(sum(fol) / len(fol), 4), fade=round(sum(fad) / len(fad), 4), priced=len(priced))
    return res


def same_move(fd, league_rows, fx, ref, limit=20):
    out = {}
    ah = (ref or {}).get("ah")
    if ah:
        ol, cl = ah["open"]["line"], ah["now"]["line"]
        rows = (fd.rows.get(fx["league"]) if fd is not None else None) or []
        fdm = [r for r in rows if r["d"] < fx["start"] and r.get("ah") == ol and r.get("ahc") == cl]
        if rows and any(r.get("ahc") is not None for r in rows):
            b = _ah_block(fdm, ol, cl, lambda r: (r["hg"] - r["ag"], r["ahc"], r.get("ahco")), limit, "football-data（2019 起有收盘盘口）")
            b["list"] = [_row_fd(r) for r in sorted(fdm, key=lambda r: -r["d"])[:limit]]
        else:
            sim = [r for r in league_rows if r.get("ah") and r["ah"]["ol"] == ol and r["ah"]["cl"] == cl]
            b = _ah_block(sim, ol, cl, lambda r: (r["hs"] - r["as"], r["ah"]["cl"], r["ah"]["codds"]), limit, "本站历史（2026 起）")
            b["list"] = [_row_pp(r) for r in sorted(sim, key=lambda r: -r["t"])[:limit]]
        out["ah"] = b
    ou = (ref or {}).get("ou")
    if ou:
        ol, cl = ou["open"]["line"], ou["now"]["line"]
        sim = [r for r in league_rows if r.get("ou") and r["ou"]["ol"] == ol and r["ou"]["cl"] == cl]
        res = {"from": ol, "to": cl, "n": len(sim), "src": "本站历史（2026 起）"}
        if sim:
            goals = [r["hs"] + r["as"] for r in sim]
            sc = [H.settle_over(g, cl, 2.0) for g in goals]
            res.update(over=round(sum(x > 0 for x in sc) / len(sc), 3), under=round(sum(x < 0 for x in sc) / len(sc), 3),
                       push=round(sum(x == 0 for x in sc) / len(sc), 3), over_k=sum(x > 0 for x in sc),
                       under_k=sum(x < 0 for x in sc), avg_goals=round(sum(goals) / len(goals), 2), follow=None, fade=None)
            k = H.steps(ol, cl)
            if k:
                fol = [(H.settle_over if k > 0 else H.settle_under)(r["hs"] + r["as"], r["ou"]["cl"],
                       r["ou"]["codds"][0 if k > 0 else 1]) for r in sim]
                fad = [(H.settle_under if k > 0 else H.settle_over)(r["hs"] + r["as"], r["ou"]["cl"],
                       r["ou"]["codds"][1 if k > 0 else 0]) for r in sim]
                res.update(follow=round(sum(fol) / len(fol), 4), fade=round(sum(fad) / len(fad), 4))
            res["list"] = [_row_pp(r) for r in sorted(sim, key=lambda r: -r["t"])[:limit]]
        out["ou"] = res
    return out


def _j(what, market, k, n, src):
    if market is None:
        return None
    hist = k / n if n else None
    est = round((K_SHRINK * market + n * hist) / (K_SHRINK + n), 4) if n else round(market, 4)
    return {"what": what, "market": round(market, 3), "hist": round(hist, 3) if hist is not None else None,
            "ci": wilson(k, n), "n": n, "est": est, "edge": round(est - market, 3), "conf": conf(n), "src": src}


def judgement(ref, so, sm, h2):
    out = []
    x = (ref or {}).get("1x2")
    lg = (so or {}).get("league") or {}
    if x:
        mk = x["now"]["p"]
        n = lg.get("n", 0)
        for i, (nm, key) in enumerate((("主胜", "w"), ("平局", "d"), ("客胜", "l"))):
            out.append(_j(nm, mk[i], lg.get(key, 0), n, "同赔（联赛）"))
        ht = (so or {}).get("home_team") or {}
        if ht.get("n"):
            out.append(_j("主胜（主队同赔）", mk[0], ht["w"], ht["n"], "同赔（本队）"))
        if h2 and h2.get("n"):
            out.append(_j("主队赢交手", mk[0], h2["w"], h2["n"], "交手"))
    ou = (ref or {}).get("ou")
    m = (sm or {}).get("ou") or {}
    if ou:
        n = m.get("over_k", 0) + m.get("under_k", 0)
        out.append(_j(f"大 {ou['now']['line']}", ou["now"]["p_over"], m.get("over_k", 0), n, "同升降"))
    a = (ref or {}).get("ah")
    m = (sm or {}).get("ah") or {}
    if a:
        n = m.get("home_k", 0) + m.get("away_k", 0)
        out.append(_j(f"主队赢盘（{H.ah_name(a['now']['line'])}）", a["now"]["p_home"], m.get("home_k", 0), n, "同升降"))
    return [j for j in out if j]


def build(fx, teams, league_rows, fd=None):
    ref = (fx.get("books") or {}).get("pinnacle") or next(iter((fx.get("books") or {}).values()), None)
    so = same_odds(fd, league_rows, fx, ref)
    sm = same_move(fd, league_rows, fx, ref)
    h2 = h2h(fd, teams, fx)
    return {"h2h": h2, "same_odds": so, "same_move": sm, "judge": judgement(ref, so, sm, h2),
            "league_n": len(league_rows)}
