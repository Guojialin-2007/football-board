"""Odds history -> what the market thinks now, what it thought at the open, and how it moved.

Markets (full time only): 1X2, Asian handicap (line for the home side = participant 1), over/under goals.
For each bookmaker we keep every timestamped price, then summarise:
  1X2   de-vigged home / draw / away probabilities, open -> now
  AH    the main line (home price nearest 50/50), the home cover probability, and the home side's expected
        goal difference implied by line + price, open -> now
  OU    the main line (nearest 50/50) and the market's EXPECTED GOALS, backed out of the .5 lines with a
        Poisson model, so a line move (2.5 -> 2.75) and a price move (over 1.95 -> 1.80) read as one number
"""

from __future__ import annotations

import math
import re

PARTIAL = re.compile(r"half|1st|2nd|first|second|corner|card|booking|team total|player|minute|period|extra|"
                     r"penalt|to qualify|clean|both teams|btts|exact|correct|odd|even|interval|race|margin|"
                     r"european|3[- ]?way handicap|draw no bet|double chance|team ?[12]\b|team ?total|\b\d+ ?up\b", re.I)
FULL = ("result", "fulltime", "full time", "ft", "regular", "fulltime result", "")


def _label(n):
    n = str(n).strip().lower()
    if n in ("1", "home", "w1", "p1"):
        return "home"
    if n in ("x", "draw", "tie"):
        return "draw"
    if n in ("2", "away", "w2", "p2"):
        return "away"
    if n.startswith("over") or n == "o":
        return "over"
    if n.startswith("under") or n == "u":
        return "under"
    return None


def classify(markets, sid):
    """{marketId: {"kind": "1x2"|"ah"|"ou", "line": float|None, "outcomes": {outcomeId: label}}}"""
    out = {}
    for m in markets:
        if sid is not None and m.get("sportId") != sid:
            continue
        if m.get("playerProp"):
            continue
        nm = str(m.get("marketName", ""))
        typ = str(m.get("marketType", "")).lower()
        per = str(m.get("period", "")).lower()
        if per not in FULL or PARTIAL.search(nm) or "team" in typ or re.search(r"\dup", typ):
            continue
        outs = {str(o.get("outcomeId")): _label(o.get("outcomeName")) for o in m.get("outcomes") or []}
        labels = set(outs.values())
        h = m.get("handicap")
        low = nm.lower()
        if labels == {"home", "draw", "away"} and len(outs) == 3 and ("1x2" in typ or "1x2" in low or "result" in low
                                                                     or "winner" in low or "moneyline" in typ):
            out[str(m["marketId"])] = {"kind": "1x2", "line": None, "outcomes": outs}
        elif labels == {"over", "under"} and len(outs) == 2 and h is not None and ("total" in typ or "over" in low
                                                                                 or "total" in low or "goals" in low):
            out[str(m["marketId"])] = {"kind": "ou", "line": float(h), "outcomes": outs}
        elif labels == {"home", "away"} and len(outs) == 2 and h is not None and (
                "handicap" in typ or "spread" in typ or "asian" in low or "handicap" in low or "spread" in low):
            out[str(m["marketId"])] = {"kind": "ah", "line": float(h), "outcomes": outs}
    return out


def compact(js, cls, books):
    """Raw /v4/historical-odds -> {book: {"1x2": {lab: [[t, price]]}, "ou": {line: {...}}, "ah": {line: {...}}}}"""
    from .papi import ts
    root = next(iter(js.values())) if isinstance(js, dict) and len(js) == 1 and "bookmakers" not in js else js
    bks = (root or {}).get("bookmakers") or {}
    res = {}
    for book in books:
        bk = bks.get(book) or {}
        d = {"1x2": {}, "ou": {}, "ah": {}}
        for mid, m in (bk.get("markets") or {}).items():
            info = cls.get(str(mid))
            if not info:
                continue
            dest = d["1x2"] if info["kind"] == "1x2" else d[info["kind"]].setdefault(str(info["line"]), {})
            for oid, oc in (m.get("outcomes") or {}).items():
                lab = info["outcomes"].get(str(oid))
                if not lab:
                    continue
                arr = dest.setdefault(lab, [])
                for entries in (oc.get("players") or {}).values():
                    for en in entries or []:
                        t = ts(en.get("createdAt"))
                        try:
                            p = float(en.get("price"))
                        except (TypeError, ValueError):
                            continue
                        if t and p > 1.0 and en.get("active", True) is not False:
                            arr.append([t, p])
        for grp in [d["1x2"]] + list(d["ou"].values()) + list(d["ah"].values()):
            for arr in grp.values():
                arr.sort()
        if d["1x2"] or d["ou"] or d["ah"]:
            res[book] = d
    return res


def _at(arr, t):
    """Last price at or before t."""
    v = None
    for tt, p in arr:
        if tt > t:
            break
        v = p
    return v


def devig(prices):
    inv = [1 / p for p in prices]
    s = sum(inv)
    return [x / s for x in inv]


def _times(groups):
    ts_ = sorted({t for g in groups for arr in g.values() for t, _ in arr})
    return ts_


def pois_over(L, lam):
    """P(total goals > L) for a .5 line."""
    k = int(math.floor(L))
    term = s = math.exp(-lam)
    for i in range(1, k + 1):
        term *= lam / i
        s += term
    return 1 - s


def lam_from(L, p_over):
    lo, hi = 0.05, 9.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if pois_over(L, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


def state_at(bd, t):
    """Everything the bookmaker shows at time t (probabilities de-vigged)."""
    st = {}
    x = bd.get("1x2") or {}
    if all(k in x for k in ("home", "draw", "away")):
        pr = [_at(x[k], t) for k in ("home", "draw", "away")]
        if all(pr):
            st["1x2"] = {"p": [round(v, 4) for v in devig(pr)], "odds": pr}
    ou = {}
    for L, g in (bd.get("ou") or {}).items():
        o, u = _at(g.get("over", []), t), _at(g.get("under", []), t)
        if o and u:
            ou[float(L)] = {"p_over": devig([o, u])[0], "odds": [o, u]}
    if ou:
        main = min(ou, key=lambda L: abs(ou[L]["p_over"] - 0.5))
        halves = {L: v for L, v in ou.items() if abs(L - math.floor(L) - 0.5) < 1e-9}
        lam = None
        if halves:
            L5 = min(halves, key=lambda L: abs(halves[L]["p_over"] - 0.5))
            lam = lam_from(L5, halves[L5]["p_over"])
        st["ou"] = {"line": main, "p_over": round(ou[main]["p_over"], 4), "odds": ou[main]["odds"], "lam": lam}
    ah = {}
    for L, g in (bd.get("ah") or {}).items():
        h, a = _at(g.get("home", []), t), _at(g.get("away", []), t)
        if h and a:
            ah[float(L)] = {"p_home": devig([h, a])[0], "odds": [h, a]}
    if ah:
        main = min(ah, key=lambda L: abs(ah[L]["p_home"] - 0.5))
        # one number for "how strong the home side is", so a line move and a price move read the same way:
        # expected goal difference ~ -line + (p_home - 0.5) * 3.8  (near 50/50 a quarter-goal is worth ~6.5%)
        sup = -main + (ah[main]["p_home"] - 0.5) * 3.8
        st["ah"] = {"line": main, "p_home": round(ah[main]["p_home"], 4), "odds": ah[main]["odds"], "sup": round(sup, 3)}
    return st


def last_move(bd, kind, end):
    """Time of the last main-line change of 'ou' or 'ah' at or before `end` (None if the line never moved)."""
    groups = list((bd.get(kind) or {}).values())
    prev, last = None, None
    for t in _times(groups):
        if t > end:
            break
        s = state_at(bd, t).get(kind)
        if not s:
            continue
        if prev is not None and s["line"] != prev:
            last = t
        prev = s["line"]
    return last


def summarize(bd, now, kickoff, points=24):
    """Open (first moment each market had prices), now (latest before min(now, kickoff)), and a series."""
    end = min(now, kickoff) if kickoff else now
    out = {}
    for kind in ("1x2", "ou", "ah"):
        groups = [bd.get("1x2") or {}] if kind == "1x2" else list((bd.get(kind) or {}).values())
        times = [t for t in _times(groups) if t <= end]
        if not times:
            continue
        first = None
        for t in times:
            s = state_at(bd, t).get(kind)
            if s:
                first = (t, s)
                break
        if not first:
            continue
        cur = state_at(bd, end).get(kind)
        if not cur:
            continue
        step = max(1, len(times) // points)
        series = []
        for t in times[::step] + [end]:
            s = state_at(bd, t).get(kind)
            if not s:
                continue
            if kind == "1x2":
                v = s["p"][0]                       # home win probability
            elif kind == "ou":
                v = s.get("lam") or s["line"]       # expected goals (or the line when no .5 line is priced)
            else:
                v = s["sup"]                        # home side's expected goal difference
            series.append([t, round(v, 4)])
        out[kind] = {"open_t": first[0], "open": first[1], "now": cur, "series": series,
                     "changes": len(times)}
        if kind in ("ou", "ah"):
            out[kind]["last_move_t"] = last_move(bd, kind, end)
    return out
