"""Per-match detail: head-to-head, "same odds" and "same line move" matches from the same league, and a
self-updating judgement that leans on history only as far as the sample size allows.

Same league = same Chinese league name from board/leagues.py (history rows carry the OddsPapi tournamentId).
  同赔      opening 1X2 odds within +-0.08 on home, draw and away (Pinnacle)
  同升降    same opening -> closing line, e.g. 主让半球 -> 主让半一 (handicap) or 2.5 -> 2.75 (over/under)
  判断      estimate = (k * market + n * history) / (k + n), k = 40: with 10 similar matches history moves the
            number a little, with 200 it dominates. Shown only from 15 matches on, always next to the market.
"""

from __future__ import annotations

from . import history as H

K_SHRINK = 40


def _wdl(rows):
    w = sum(r["hs"] > r["as"] for r in rows)
    d = sum(r["hs"] == r["as"] for r in rows)
    return [w, d, len(rows) - w - d]


def _brief(r, extra=None):
    out = {"t": r["t"], "home": r["home"], "away": r["away"], "score": f"{r['hs']}-{r['as']}"}
    if r.get("x2", {}).get("oodds"):
        out["odds"] = [round(x, 2) for x in r["x2"]["oodds"]]
    if r.get("ah"):
        out["ah"] = [r["ah"]["ol"], r["ah"]["cl"]]
    if r.get("ou"):
        out["ou"] = [r["ou"]["ol"], r["ou"]["cl"]]
    if extra:
        out.update(extra)
    return out


def _shrink(market, hist, n):
    if market is None or hist is None or n == 0:
        return None
    return round((K_SHRINK * market + n * hist) / (K_SHRINK + n), 4)


def h2h(teams, fx, limit=8):
    a, b = teams.find(fx["home"]), teams.find(fx["away"])
    if not a or not b:
        return None
    ms = [r for r in teams.res if r["t"] < fx["start"] and {r["home"][0], r["away"][0]} == {a, b}]
    ms = ms[-limit:][::-1]
    out = []
    for r in ms:
        out.append({"t": r["t"], "home": r["home"][0], "away": r["away"][0], "score": f"{r['hs']}-{r['as']}",
                    "lg": r.get("lg")})
    hw = sum((r["home"][0] == a and r["hs"] > r["as"]) or (r["away"][0] == a and r["as"] > r["hs"]) for r in ms)
    aw = sum((r["home"][0] == b and r["hs"] > r["as"]) or (r["away"][0] == b and r["as"] > r["hs"]) for r in ms)
    goals = [r["hs"] + r["as"] for r in ms]
    return {"n": len(ms), "home_w": hw, "draw": len(ms) - hw - aw, "away_w": aw,
            "avg_goals": round(sum(goals) / len(goals), 2) if goals else None,
            "o25": round(sum(g > 2.5 for g in goals) / len(goals), 2) if goals else None, "list": out}


def same_odds(rows, fx, ref, tol=0.08, limit=12):
    x = (ref or {}).get("1x2")
    if not x:
        return None
    o = x["open"]["odds"]
    sim = [r for r in rows if r.get("x2", {}).get("oodds")
           and all(abs(r["x2"]["oodds"][i] - o[i]) <= tol * max(1.0, o[i] / 3) for i in range(3))]
    if not sim:
        return {"n": 0, "odds": [round(v, 2) for v in o]}
    goals = [r["hs"] + r["as"] for r in sim]
    w, d, l = _wdl(sim)
    sim.sort(key=lambda r: -r["t"])
    return {"n": len(sim), "odds": [round(v, 2) for v in o], "wdl": [w, d, l],
            "avg_goals": round(sum(goals) / len(goals), 2), "o25": round(sum(g > 2.5 for g in goals) / len(goals), 2),
            "list": [_brief(r) for r in sim[:limit]]}


def same_move(rows, fx, ref, limit=12):
    out = {}
    ah = (ref or {}).get("ah")
    if ah:
        ol, cl = ah["open"]["line"], ah["now"]["line"]
        sim = [r for r in rows if r.get("ah") and r["ah"]["ol"] == ol and r["ah"]["cl"] == cl]
        res = {"from": ol, "to": cl, "n": len(sim)}
        if sim:
            sc = [H.settle_home(r["hs"] - r["as"], cl, 2.0) for r in sim]   # +1 win, +-0.5 half, 0 push, -1 loss
            res.update(home_cover=round(sum(x > 0 for x in sc) / len(sc), 3),
                       away_cover=round(sum(x < 0 for x in sc) / len(sc), 3),
                       push=round(sum(x == 0 for x in sc) / len(sc), 3), wdl=_wdl(sim),
                       follow=None, fade=None)
            k = -H.steps(ol, cl)
            if k:
                fol = [(H.settle_home if k > 0 else H.settle_away)(r["hs"] - r["as"], r["ah"]["cl"],
                       r["ah"]["codds"][0 if k > 0 else 1]) for r in sim]
                fad = [(H.settle_away if k > 0 else H.settle_home)(r["hs"] - r["as"], r["ah"]["cl"],
                       r["ah"]["codds"][1 if k > 0 else 0]) for r in sim]
                res.update(follow=round(sum(fol) / len(fol), 4), fade=round(sum(fad) / len(fad), 4))
            res["list"] = [_brief(r) for r in sorted(sim, key=lambda r: -r["t"])[:limit]]
        out["ah"] = res
    ou = (ref or {}).get("ou")
    if ou:
        ol, cl = ou["open"]["line"], ou["now"]["line"]
        sim = [r for r in rows if r.get("ou") and r["ou"]["ol"] == ol and r["ou"]["cl"] == cl]
        res = {"from": ol, "to": cl, "n": len(sim)}
        if sim:
            goals = [r["hs"] + r["as"] for r in sim]
            sc = [H.settle_over(g, cl, 2.0) for g in goals]
            res.update(over=round(sum(x > 0 for x in sc) / len(sc), 3), under=round(sum(x < 0 for x in sc) / len(sc), 3),
                       push=round(sum(x == 0 for x in sc) / len(sc), 3), avg_goals=round(sum(goals) / len(goals), 2),
                       follow=None, fade=None)
            k = H.steps(ol, cl)
            if k:
                fol = [(H.settle_over if k > 0 else H.settle_under)(r["hs"] + r["as"], r["ou"]["cl"],
                       r["ou"]["codds"][0 if k > 0 else 1]) for r in sim]
                fad = [(H.settle_under if k > 0 else H.settle_over)(r["hs"] + r["as"], r["ou"]["cl"],
                       r["ou"]["codds"][1 if k > 0 else 0]) for r in sim]
                res.update(follow=round(sum(fol) / len(fol), 4), fade=round(sum(fad) / len(fad), 4))
            res["list"] = [_brief(r) for r in sorted(sim, key=lambda r: -r["t"])[:limit]]
        out["ou"] = res
    return out


def judgement(rows, ref, so, sm, min_n=15):
    """Market probability next to what similar past matches did, blended by sample size."""
    out = []
    x = (ref or {}).get("1x2")
    if x and so and so.get("n", 0) >= min_n:
        n = so["n"]
        emp = [v / n for v in so["wdl"]]
        mk = x["now"]["p"]
        for i, nm in enumerate(("主胜", "平局", "客胜")):
            est = _shrink(mk[i], emp[i], n)
            out.append({"what": nm, "market": round(mk[i], 3), "hist": round(emp[i], 3), "n": n, "est": est,
                        "src": "同赔"})
    ou = (ref or {}).get("ou")
    m = (sm or {}).get("ou")
    if ou and m and m.get("n", 0) >= min_n:
        n = m["n"]
        mk = ou["now"]["p_over"]
        hist = m["over"] / max(1e-9, m["over"] + m["under"])
        out.append({"what": f"大 {ou['now']['line']}", "market": round(mk, 3), "hist": round(hist, 3), "n": n,
                    "est": _shrink(mk, hist, n), "src": "同升降"})
    a = (ref or {}).get("ah")
    m = (sm or {}).get("ah")
    if a and m and m.get("n", 0) >= min_n:
        n = m["n"]
        mk = a["now"]["p_home"]
        hist = m["home_cover"] / max(1e-9, m["home_cover"] + m["away_cover"])
        out.append({"what": f"主队赢盘（{H.ah_name(a['now']['line'])}）", "market": round(mk, 3), "hist": round(hist, 3),
                    "n": n, "est": _shrink(mk, hist, n), "src": "同升降"})
    for j in out:
        j["edge"] = round(j["est"] - j["market"], 3) if j["est"] is not None else None
    return out


def build(fx, teams, league_rows):
    ref = (fx.get("books") or {}).get("pinnacle") or next(iter((fx.get("books") or {}).values()), None)
    so = same_odds(league_rows, fx, ref)
    sm = same_move(league_rows, fx, ref)
    return {"h2h": h2h(teams, fx), "same_odds": so, "same_move": sm, "judge": judgement(league_rows, ref, so, sm),
            "league_n": len(league_rows)}
