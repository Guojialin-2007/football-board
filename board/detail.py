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


def _ci(v):
    """95% interval of a mean return (None under 2 samples)."""
    if len(v) < 2:
        return None
    mu = sum(v) / len(v)
    sd = (sum((y - mu) ** 2 for y in v) / (len(v) - 1)) ** 0.5
    h = 1.96 * sd / len(v) ** 0.5
    return [round(mu - h, 3), round(mu + h, 3)]


def _wk(sc, sign):
    """Weighted wins (sign=1) or losses (-1) from results settled at even money: a half win / half loss counts as
    half a match and pushes drop out, so k / (wins + losses) is directly comparable with the market's de-vigged
    probability on quarter lines (on -0.25 a draw is a half loss, not a full one)."""
    return round(sum(abs(x) for x in sc if x * sign > 0), 2)


def _ah_block(sim_rows, ol, cl, get, limit, src):
    """sim_rows: rows with the same move; get(r) -> (goal diff, closing line, closing odds [home, away])"""
    res = {"from": ol, "to": cl, "n": len(sim_rows), "src": src}
    if not sim_rows:
        return res
    sc = [H.settle_home(get(r)[0], cl, 2.0) for r in sim_rows]
    res.update(home_cover=round(sum(x > 0 for x in sc) / len(sc), 3), away_cover=round(sum(x < 0 for x in sc) / len(sc), 3),
               push=round(sum(x == 0 for x in sc) / len(sc), 3), home_k=_wk(sc, 1), away_k=_wk(sc, -1),
               follow=None, fade=None)
    k = -H.steps(ol, cl)
    priced = [r for r in sim_rows if get(r)[2]]
    if k and priced:
        fol = [(H.settle_home if k > 0 else H.settle_away)(get(r)[0], get(r)[1], get(r)[2][0 if k > 0 else 1]) for r in priced]
        fad = [(H.settle_away if k > 0 else H.settle_home)(get(r)[0], get(r)[1], get(r)[2][1 if k > 0 else 0]) for r in priced]
        res.update(follow=round(sum(fol) / len(fol), 4), fade=round(sum(fad) / len(fad), 4), priced=len(priced),
                   follow_ci=_ci(fol), fade_ci=_ci(fad))
    return res


def _dp(o, c):
    """Change of the first side's de-vigged probability from opening odds o to closing odds c."""
    if not o or not c or not all(o) or not all(c):
        return None
    f = lambda x: (1 / x[0]) / (1 / x[0] + 1 / x[1])
    return f(c) - f(o)


WATER_TOL = 0.03          # ~0.12 in decimal odds near evens


def water(dp, side):
    if dp is None:
        return None
    return f"{side}水位降" if dp >= WATER_TOL else f"{side}水位升" if dp <= -WATER_TOL else "水位基本不变"


def timing(hours):
    if hours is None:
        return "盘口没变"
    return "开赛前 3 小时内" if hours < 3 else "开赛前 3–24 小时" if hours < 24 else "开赛 24 小时以前"


def _now_timing(fx, m):
    if "last_move_t" not in (m or {}):
        return None
    t = m["last_move_t"]
    return timing((fx["start"] - t) / 3600 if t else None)


def _sub(block_fn, rows, key_fn, want, ol, cl, get, src):
    sub = [r for r in rows if key_fn(r) == want]
    b = block_fn(sub, ol, cl, get, 0, src)
    b["cls"] = want
    return b


def _ou_block(sim, ol, cl, src):
    res = {"from": ol, "to": cl, "n": len(sim), "src": src}
    if not sim:
        return res
    goals = [r["hs"] + r["as"] for r in sim]
    sc = [H.settle_over(g, cl, 2.0) for g in goals]
    res.update(over=round(sum(x > 0 for x in sc) / len(sc), 3), under=round(sum(x < 0 for x in sc) / len(sc), 3),
               push=round(sum(x == 0 for x in sc) / len(sc), 3), over_k=_wk(sc, 1),
               under_k=_wk(sc, -1), avg_goals=round(sum(goals) / len(goals), 2), follow=None, fade=None)
    k = H.steps(ol, cl)
    if k:
        fol = [(H.settle_over if k > 0 else H.settle_under)(r["hs"] + r["as"], r["ou"]["cl"],
               r["ou"]["codds"][0 if k > 0 else 1]) for r in sim]
        fad = [(H.settle_under if k > 0 else H.settle_over)(r["hs"] + r["as"], r["ou"]["cl"],
               r["ou"]["codds"][1 if k > 0 else 0]) for r in sim]
        res.update(follow=round(sum(fol) / len(fol), 4), fade=round(sum(fad) / len(fad), 4),
                   follow_ci=_ci(fol), fade_ci=_ci(fad))
    return res


def same_move(fd, league_rows, fx, ref, limit=20, all_rows=None):
    """Exactly the same opening -> closing line (2.5 -> 2.75 is not 2.75 -> 3), in the same league, then narrowed
    by the water (price) move and by when the line last moved; plus the same move for each team (home team at home,
    away team away, any league). football-data has opening/closing prices but no times, so timing uses the board's
    own history."""
    out = {}
    all_rows = all_rows if all_rows is not None else league_rows
    ah = (ref or {}).get("ah")
    if ah:
        ol, cl = ah["open"]["line"], ah["now"]["line"]
        w_now = water(_dp(ah["open"].get("odds"), ah["now"].get("odds")), "主队")
        t_now = _now_timing(fx, ah)
        rows = (fd.rows.get(fx["league"]) if fd is not None else None) or []
        own = [r for r in league_rows if r.get("ah") and r["ah"]["ol"] == ol and r["ah"]["cl"] == cl]
        get_pp = lambda r: (r["hs"] - r["as"], r["ah"]["cl"], r["ah"]["codds"])
        w_pp = lambda r: water(_dp(r["ah"].get("oodds"), r["ah"].get("codds")), "主队")
        if rows and any(r.get("ahc") is not None for r in rows):
            fdm = [r for r in rows if r["d"] < fx["start"] and r.get("ah") == ol and r.get("ahc") == cl]
            get = lambda r: (r["hg"] - r["ag"], r["ahc"], r.get("ahco"))
            src = "football-data（2019 起有收盘盘口）"
            b = _ah_block(fdm, ol, cl, get, limit, src)
            b["list"] = [_row_fd(r) for r in sorted(fdm, key=lambda r: -r["d"])[:limit]]
            if w_now:
                b["water"] = _sub(_ah_block, fdm, lambda r: water(_dp(r.get("aho"), r.get("ahco")), "主队"), w_now,
                                  ol, cl, get, src)
            ta, tb = fd.team(fx["home"], fx["league"]), fd.team(fx["away"], fx["league"])
            allm = [r for r in fd.all_rows() if r["d"] < fx["start"] and r.get("ah") == ol and r.get("ahc") == cl]
            for key, nm, pick in (("home_team", ta, lambda r, t: r["h"] == t), ("away_team", tb, lambda r, t: r["a"] == t)):
                if nm:
                    tr = [r for r in allm if pick(r, nm)]
                    b[key] = dict(_ah_block(tr, ol, cl, get, 0, "football-data（近 20 年，各联赛）"), team=nm,
                                  list=[_row_fd(r) for r in sorted(tr, key=lambda r: -r["d"])[:limit]])
        else:
            src = "本站历史（2026 起）"
            b = _ah_block(own, ol, cl, get_pp, limit, src)
            b["list"] = [_row_pp(r) for r in sorted(own, key=lambda r: -r["t"])[:limit]]
            if w_now:
                b["water"] = _sub(_ah_block, own, w_pp, w_now, ol, cl, get_pp, src)
            allo = [r for r in all_rows if r.get("ah") and r["ah"]["ol"] == ol and r["ah"]["cl"] == cl]
            for key, nm, side in (("home_team", fx["home"], "home"), ("away_team", fx["away"], "away")):
                tr = [r for r in allo if r[side] == nm]
                b[key] = dict(_ah_block(tr, ol, cl, get_pp, 0, "本站历史（2026 起，各联赛）"), team=nm,
                              list=[_row_pp(r) for r in sorted(tr, key=lambda r: -r["t"])[:limit]])
        timed = [r for r in own if "lt" in r["ah"]]
        if t_now:
            b["timing"] = _sub(_ah_block, timed, lambda r: timing(r["ah"]["lt"]), t_now, ol, cl, get_pp,
                           "本站历史（记录变盘时间，2026-11 起）")
        out["ah"] = b
    ou = (ref or {}).get("ou")
    if ou:
        ol, cl = ou["open"]["line"], ou["now"]["line"]
        sim = [r for r in league_rows if r.get("ou") and r["ou"]["ol"] == ol and r["ou"]["cl"] == cl]
        src = "本站历史（2026 起）"
        res = _ou_block(sim, ol, cl, src)
        if sim:
            res["list"] = [_row_pp(r) for r in sorted(sim, key=lambda r: -r["t"])[:limit]]
        w_now = water(_dp(ou["open"].get("odds"), ou["now"].get("odds")), "大球")
        if w_now:
            sub = [r for r in sim if water(_dp(r["ou"].get("oodds"), r["ou"].get("codds")), "大球") == w_now]
            res["water"] = dict(_ou_block(sub, ol, cl, src), cls=w_now)
        t_now = _now_timing(fx, ou)
        sub = [r for r in sim if "lt" in r["ou"] and timing(r["ou"]["lt"]) == t_now]
        if t_now:
            res["timing"] = dict(_ou_block(sub, ol, cl, "本站历史（记录变盘时间，2026-11 起）"), cls=t_now)
        allo = [r for r in all_rows if r.get("ou") and r["ou"]["ol"] == ol and r["ou"]["cl"] == cl]
        for key, nm, side in (("home_team", fx["home"], "home"), ("away_team", fx["away"], "away")):
            tr = [r for r in allo if r[side] == nm]
            res[key] = dict(_ou_block(tr, ol, cl, "本站历史（2026 起，各联赛）"), team=nm,
                            list=[_row_pp(r) for r in sorted(tr, key=lambda r: -r["t"])[:limit]])
        out["ou"] = res
    return out


def _j(what, market, k, n, src, bet=None):
    """bet = ((label if it happens, odds), (label if it doesn't, odds) or None) for turning an edge into a pick."""
    if market is None:
        return None
    hist = k / n if n else None
    est = round((K_SHRINK * market + n * hist) / (K_SHRINK + n), 4) if n else round(market, 4)
    return {"what": what, "market": round(market, 3), "hist": round(hist, 3) if hist is not None else None,
            "ci": wilson(k, n), "n": round(n, 1), "est": est, "edge": round(est - market, 3), "conf": conf(n), "src": src,
            "bet": bet}


def judgement(ref, so, sm, h2):
    out = []
    x = (ref or {}).get("1x2")
    lg = (so or {}).get("league") or {}
    if x:
        mk = x["now"]["p"]
        n = lg.get("n", 0)
        od = x["now"].get("odds")
        for i, (nm, key) in enumerate((("主胜", "w"), ("平局", "d"), ("客胜", "l"))):
            out.append(_j(nm, mk[i], lg.get(key, 0), n, "同赔（联赛）", ((f"入手{nm}", od[i]), None) if od else None))
        ht = (so or {}).get("home_team") or {}
        if ht.get("n"):
            out.append(_j("主胜（主队同赔）", mk[0], ht["w"], ht["n"], "同赔（本队）", (("入手主胜", od[0]), None) if od else None))
        if h2 and h2.get("n"):
            out.append(_j("主队赢交手", mk[0], h2["w"], h2["n"], "交手"))
    ou = (ref or {}).get("ou")
    m = (sm or {}).get("ou") or {}
    if ou:
        L, oo = ou["now"]["line"], ou["now"].get("odds")
        ob = ((f"买大 {L:g}", oo[0]), (f"买小 {L:g}", oo[1])) if oo else None
        n = m.get("over_k", 0) + m.get("under_k", 0)
        out.append(_j(f"大 {ou['now']['line']}", ou["now"]["p_over"], m.get("over_k", 0), n, "同升降", ob))
        for key, lab in (("home_team", "同升降（主队）"), ("away_team", "同升降（客队）")):
            sb = m.get(key)
            if sb and sb.get("n"):
                out.append(_j(f"大 {ou['now']['line']}（{sb['team']} 同升降）", ou["now"]["p_over"], sb.get("over_k", 0),
                              sb.get("over_k", 0) + sb.get("under_k", 0), lab, ob))
        for key, lab in (("water", "同升降+同水位"), ("timing", "同升降+同变盘时间")):
            sb = m.get(key)
            if sb:
                out.append(_j(f"大 {ou['now']['line']}（同升降，{sb['cls']}）", ou["now"]["p_over"], sb.get("over_k", 0),
                              sb.get("over_k", 0) + sb.get("under_k", 0), lab, ob))
    a = (ref or {}).get("ah")
    m = (sm or {}).get("ah") or {}
    if a:
        ao = a["now"].get("odds")
        ab = ((f"买主队（{H.ah_name(a['now']['line'])}）", ao[0]), ("买客队", ao[1])) if ao else None
        n = m.get("home_k", 0) + m.get("away_k", 0)
        out.append(_j(f"主队赢盘（{H.ah_name(a['now']['line'])}）", a["now"]["p_home"], m.get("home_k", 0), n, "同升降", ab))
        for key, lab in (("home_team", "同升降（主队）"), ("away_team", "同升降（客队）")):
            sb = m.get(key)
            if sb and sb.get("n"):
                out.append(_j(f"主队赢盘（{sb['team']} 同升降）", a["now"]["p_home"], sb.get("home_k", 0),
                              sb.get("home_k", 0) + sb.get("away_k", 0), lab, ab))
        for key, lab in (("water", "同升降+同水位"), ("timing", "同升降+同变盘时间")):
            sb = m.get(key)
            if sb:
                out.append(_j(f"主队赢盘（同升降，{sb['cls']}）", a["now"]["p_home"], sb.get("home_k", 0),
                              sb.get("home_k", 0) + sb.get("away_k", 0), lab, ab))
    return [j for j in out if j]


BT_NOTE = "回测：这类历史偏差在近 3 年 2500 场里没有赚钱（实际 −13%），只当参考"
MIN_EV = 0.02           # a pick must be worth at least +2% per stake at today's price
MIN_N = 30              # and rest on at least 30 past matches (confidence 中 or 高)


def highlights(fx, ref, judge, sm, limit=3):
    """The few things worth noticing, instead of every number: picks with a real edge first, then warnings.
    A history pick needs >= MIN_N matches, a 95% interval that excludes the market's probability, and an
    expected return >= MIN_EV after shrinking the history towards the market (so it is conservative)."""
    picks = {}

    def add(label, p):
        if label not in picks or p["ev"] > picks[label]["ev"]:
            picks[label] = p

    for j in judge:
        if not j.get("bet") or j["n"] < MIN_N or not j.get("ci"):
            continue
        yes, no = j["bet"]
        for side, (bet, odds) in ((1, yes), (0, no)) if no else ((1, yes),):
            p = j["est"] if side else 1 - j["est"]
            mk = j["market"] if side else 1 - j["market"]
            sig = j["ci"][0] > j["market"] if side else j["ci"][1] < j["market"]
            ev = p * odds - 1
            if sig and ev >= MIN_EV:
                h = j["hist"] if side else 1 - j["hist"]
                lo, hi = (j["ci"][0], j["ci"][1]) if side else (1 - j["ci"][1], 1 - j["ci"][0])
                add(bet, {"type": "watch", "bet": bet, "odds": odds, "src": j["src"],
                          "title": f"留意：{bet}（赔率 {odds:.2f}）", "ev": round(ev, 4), "conf": j["conf"],
                          "why": f"市场 {mk * 100:.0f}%，{j['src']} {j['n']:g} 场里 {h * 100:.0f}%"
                                 f"（95% 区间 {lo * 100:.0f}–{hi * 100:.0f}%），综合估计 {p * 100:.0f}%，"
                                 f"按历史估算每注 {ev * 100:+.1f}%。{BT_NOTE}"})
    for kind in ("ou", "ah"):                                 # exact same line move, same league: follow or fade
        b = (sm or {}).get(kind) or {}
        if b.get("from") == b.get("to") or (b.get("n") or 0) < MIN_N or b.get("follow") is None:
            continue
        k = H.steps(b["from"], b["to"]) * (1 if kind == "ou" else -1)
        if kind == "ou":
            fol, fad = (f"买大 {b['to']:g}", f"买小 {b['to']:g}") if k > 0 else (f"买小 {b['to']:g}", f"买大 {b['to']:g}")
            mv = f"大小球 {b['from']:g}→{b['to']:g}"
        else:
            hn = f"买主队（{H.ah_name(b['to'])}）"
            fol, fad = (hn, "买客队") if k > 0 else ("买客队", hn)
            mv = f"亚盘 {H.ah_name(b['from'])}→{H.ah_name(b['to'])}"
        for key, bet, tag in (("follow", fol, "顺着变盘"), ("fade", fad, "诱盘迹象，反着买")):
            ci = b.get(key + "_ci")
            if ci and ci[0] > 0 and b[key] >= MIN_EV:
                add(bet, {"type": "watch", "bet": bet, "src": key, "title": f"留意：{bet}（{tag}）", "ev": b[key],
                          "conf": conf(b["n"]),
                          "why": f"同联赛 {mv} 一模一样的变盘 {b['n']} 场，这样买平均每注 {b[key] * 100:+.1f}%"
                                 f"（95% 区间 {ci[0] * 100:+.0f}% ~ {ci[1] * 100:+.0f}%）。{BT_NOTE}"})
    for f in fx.get("flags") or []:                           # a soft book paying more than Pinnacle's fair price
        if f.get("k") == "value" and f.get("ev", 0) >= 0.03:
            add(f["bet"] + "@" + f["book"], {"type": "bet", "title": f"{f['bet']}（{f['book']} 赔率 {f['odds']:.2f}）",
                                             "ev": f["ev"], "conf": "价格",
                                             "why": f"比 Pinnacle 去水后的公平价高 {f['ev'] * 100:.0f}%（价格差，不靠历史）"})
    out = sorted(picks.values(), key=lambda p: (p["type"] != "bet", -p["ev"]))[:limit]
    warns = []
    for f in fx.get("flags") or []:
        if f.get("k") == "conflict" or (f.get("k") in ("ou", "ah", "1x2") and f.get("lv", 0) >= 3):
            warns.append({"type": "warn", "title": "注意" if f["k"] == "conflict" else "盘口大幅异动", "why": f["t"]})
    out += warns[:max(0, limit - len(out))] if out else warns[:2]
    if not any(p["type"] == "bet" for p in out):
        out.insert(0, {"type": "none", "title": "没有值得下手的价格",
                       "why": "没有哪家庄的赔率高过 Pinnacle 的公平价；下面的留意点只是历史偏差，回测没证明能赚钱"
                       if out else "市场价格和历史同类比赛基本一致，不建议硬找理由下注"})
    return out


def build(fx, teams, league_rows, fd=None, all_rows=None):
    ref = (fx.get("books") or {}).get("pinnacle") or next(iter((fx.get("books") or {}).values()), None)
    so = same_odds(fd, league_rows, fx, ref)
    sm = same_move(fd, league_rows, fx, ref, all_rows=all_rows)
    h2 = h2h(fd, teams, fx)
    jd = judgement(ref, so, sm, h2)
    return {"h2h": h2, "same_odds": so, "same_move": sm, "judge": jd, "points": highlights(fx, ref, jd, sm),
            "league_n": len(league_rows)}
