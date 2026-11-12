"""这场比赛的倾向: evidence for this particular match, laid out side by side.

Not a formula that promises profit. For each market (大小球, 让球) it collects what history says about a move like
this one, for this league and these two teams:

  exact    same league, exactly the same opening and current line (own history; football-data for handicaps)
  allx     every league, exactly the same opening and current line
  lgdir    same league, the market moved the same way (up / down / stayed)
  home_t   this home side at home when the market moved this way
  away_t   this away side away when the market moved this way

Every piece counts results the way the bet settles (half wins count half, pushes drop out) and compares the share
that came in with what the closing price implied. A piece "supports" a side when that side came in clearly more
often than priced (>= 6 points, z >= 1; z >= 2 counts double). The lean is the net of the votes:
  1 略偏   2-3 偏   4+ 明显偏   0 看不出方向

Honest record: board/lean.py::backtest replays the same votes walk-forward on all history (36,000 matches) and the
page shows the result next to every lean. Followed blindly at closing prices they win about half the time - they
describe the evidence for a match, they are not a proven edge. Live leans are also tracked in 信号战绩.
"""

from __future__ import annotations

import collections
import math

from . import history as H
from .odds import devig, lam_from

AH_SIDES = ("主队", "客队")
OU_SIDES = ("大球", "小球")
DIR_TXT = {"ah": {"home": "盘口往主队方向动", "away": "盘口往客队方向动", "flat-home": "盘口没变、主队水位降",
                  "flat-away": "盘口没变、客队水位降", "flat": "盘口和水位基本没变"},
           "ou": {"up": "预期进球升", "down": "预期进球降", "flat": "预期进球基本没变"}}
NMIN = {"exact": 15, "allx": 60, "lgdir": 30, "home_t": 8, "away_t": 8}
EX_MIN = 0.06


def ah_dir(ol, cl, oo=None, co=None):
    k = H.steps(ol, cl)                         # home line more negative = more goals given by the home side
    if k < 0:
        return "home"
    if k > 0:
        return "away"
    if oo and co:
        d = co[0] - oo[0]
        if d <= -0.06:
            return "flat-home"
        if d >= 0.06:
            return "flat-away"
    return "flat"


def ou_dir(olam, clam):
    d = (clam or 0) - (olam or 0)
    return "up" if d >= 0.08 else "down" if d <= -0.08 else "flat"


def unified(fd, own_rows):
    """One row format for football-data (src 'fd') and the board's own history (src 'own'), oldest first.
    own rows need r['lgz'] (the board's league name) set by the caller."""
    out = []
    for lg, rs in (fd.rows.items() if fd is not None else []):
        for r in rs:
            u = {"t": r["d"], "lg": lg, "h": r["h"], "a": r["a"], "hg": r["hg"], "ag": r["ag"], "src": "fd"}
            if r.get("ah") is not None and r.get("ahc") is not None and r.get("ahco") and min(r["ahco"]) > 1.05:
                u["ah"] = {"ol": r["ah"], "cl": r["ahc"], "co": r["ahco"], "oo": r.get("aho")}
            if r.get("ou") and r.get("ouc") and min(r["ou"] + r["ouc"]) > 1.05:
                u["ou"] = {"ol": 2.5, "cl": 2.5, "olam": lam_from(2.5, devig(r["ou"])[0]),
                           "clam": lam_from(2.5, devig(r["ouc"])[0]), "co": r["ouc"]}
            if "ah" in u or "ou" in u:
                out.append(u)
    for r in own_rows or []:
        u = {"t": r["t"], "lg": r.get("lgz") or r.get("lg"), "h": r["home"], "a": r["away"], "hg": r["hs"], "ag": r["as"],
             "src": "own"}
        m = r.get("ah")
        if m and m.get("codds") and min(m["codds"]) > 1.05:
            u["ah"] = {"ol": m["ol"], "cl": m["cl"], "co": m["codds"], "oo": m.get("oodds")}
        m = r.get("ou")
        if m and m.get("codds") and min(m["codds"]) > 1.05 and m.get("olam") and m.get("clam"):
            u["ou"] = {"ol": m["ol"], "cl": m["cl"], "olam": m["olam"], "clam": m["clam"], "co": m["codds"]}
        if "ah" in u or "ou" in u:
            out.append(u)
    out.sort(key=lambda u: u["t"])
    return out


class Stat:
    """Results for side a (home cover / over): weighted wins, weighted losses, and what the closing price implied."""
    __slots__ = ("n", "w", "l", "p")

    def __init__(self):
        self.n = 0
        self.w = self.l = self.p = 0.0

    def add(self, even, p_a):
        self.n += 1
        if even > 0:
            self.w += even
        elif even < 0:
            self.l -= even
        self.p += p_a

    def view(self):
        tot = self.w + self.l
        if not self.n or tot <= 0:
            return None
        rate, imp = self.w / tot, self.p / self.n
        return {"n": self.n, "rate": rate, "imp": imp, "ex": rate - imp, "z": (rate - imp) * math.sqrt(tot) / 0.5}


def vote(st, kind):
    """(+1 side a / -1 side b / 0, strong?, view)"""
    v = st.view() if st else None
    if not v or v["n"] < NMIN[kind]:
        return 0, False, v
    if abs(v["ex"]) >= EX_MIN and abs(v["z"]) >= 1.0:
        return (1 if v["ex"] > 0 else -1), abs(v["z"]) >= 2.0, v
    return 0, False, v


def keys_for(mk, src, lg, home, away, ol, cl, d, exact_ok=True):
    return {"exact": (mk, "x", lg, ol, cl) if exact_ok else None,
            "allx": (mk, "X", ol, cl) if exact_ok else None,
            "lgdir": (mk, "d", lg, d),
            "home_t": (mk, "h", src, home, d),
            "away_t": (mk, "a", src, away, d)}


def _measure(u, mk):
    m = u[mk]
    diff, goals = u["hg"] - u["ag"], u["hg"] + u["ag"]
    if mk == "ah":
        even = H.settle_home(diff, m["cl"], 2.0)
        ra, rb = H.settle_home(diff, m["cl"], m["co"][0]), H.settle_away(diff, m["cl"], m["co"][1])
        d = ah_dir(m["ol"], m["cl"], m.get("oo"), m["co"])
    else:
        even = H.settle_over(goals, m["cl"], 2.0)
        ra, rb = H.settle_over(goals, m["cl"], m["co"][0]), H.settle_under(goals, m["cl"], m["co"][1])
        d = ou_dir(m["olam"], m["clam"])
    return even, devig(m["co"])[0], ra, rb, d


def grade(net):
    a = abs(net)
    return 3 if a >= 4 else 2 if a >= 2 else 1 if a >= 1 else 0


class Index:
    def __init__(self, rows):
        self.S = collections.defaultdict(Stat)
        self.n = 0
        for u in rows:
            self.add(u)

    def add(self, u):
        self.n += 1
        for mk in ("ah", "ou"):
            if mk not in u:
                continue
            even, p_a, _, _, d = _measure(u, mk)
            m = u[mk]
            for k in keys_for(mk, u["src"], u["lg"], u["h"], u["a"], m["ol"], m["cl"], d,
                              exact_ok=(mk == "ah" or u["src"] == "own")).values():
                if k is not None:
                    self.S[k].add(even, p_a)

    def get(self, k):
        return self.S.get(k) if k is not None else None


def backtest(rows, test_share=0.45):
    """Walk forward: votes from earlier matches only, bet the lean at the closing price. Per market and grade."""
    ix = Index([])
    cut = rows[int(len(rows) * (1 - test_share))]["t"] if rows else 0
    res = collections.defaultdict(list)
    for u in rows:
        if u["t"] >= cut:
            for mk in ("ah", "ou"):
                if mk not in u:
                    continue
                _, _, ra, rb, d = _measure(u, mk)
                m = u[mk]
                net = 0
                for kind, k in keys_for(mk, u["src"], u["lg"], u["h"], u["a"], m["ol"], m["cl"], d,
                                        exact_ok=(mk == "ah" or u["src"] == "own")).items():
                    v, strong, _ = vote(ix.get(k), kind)
                    net += v * (2 if strong else 1)
                if net:
                    res[(mk, grade(net))].append(ra if net > 0 else rb)
        ix.add(u)
    out = {"from": cut, "n_rows": len(rows)}
    for mk in ("ah", "ou"):
        out[mk] = {}
        for g in (1, 2, 3):
            v = res.get((mk, g)) or []
            dec = [x for x in v if x != 0]
            out[mk][str(g)] = {"n": len(v), "win": round(sum(1 for x in dec if x > 0) / len(dec), 4) if dec else None,
                               "roi": round(sum(v) / len(v), 4) if v else None}
    return out


def _pc(x):
    return f"{x * 100:.0f}%"


def _item(kind, mk, v, strong, view, where, side_names, thin):
    a = view["ex"] > 0
    side = side_names[0] if a else side_names[1]
    rate = view["rate"] if a else 1 - view["rate"]
    imp = view["imp"] if a else 1 - view["imp"]
    head = f"{where}，共 {view['n']} 场："
    if v:
        tail = f"{side}打出 {_pc(rate)}，比当时价格隐含的 {_pc(imp)} 高{'很多' if strong else ''}"
    elif abs(view["ex"]) >= 0.03:
        tail = f"{side}打出 {_pc(rate)}（价格隐含 {_pc(imp)}），{'场次太少' if thin else '差距还不够明显'}，不算数"
    else:
        tail = f"{side_names[0]}打出 {_pc(view['rate'])}，和价格隐含的 {_pc(view['imp'])} 差不多"
    return {"kind": kind, "vote": v, "strong": strong, "n": view["n"], "side": side if v else None,
            "rate": round(rate, 3), "imp": round(imp, 3), "thin": thin, "t": head + tail}


def side_line(mk, side, line):
    """What the bet is called: 主队 让半一 / 客队 受让半一 / 大球 2.75."""
    if mk == "ou":
        return f"{OU_SIDES[side]} {line:g}"
    if line == 0:
        return f"{AH_SIDES[side]} 平手"
    gives = (line < 0) == (side == 0)
    return f"{AH_SIDES[side]} {'让' if gives else '受让'}{H.AH_NAMES.get(abs(line), f'{abs(line):g}球')}"


def _line_txt(mk, line):
    return H.ah_name(line) if mk == "ah" else f"{line:g}"


def for_match(card, ix, fd=None):
    """{'ah': {...}, 'ou': {...}} or None. Each: dir (0/1 index into the side names, or None), label, grade, net,
    items (the evidence, strongest first), bet (what following the lean means right now)."""
    books = card.get("books") or {}
    ref = books.get("pinnacle")
    if not ref or ix is None:
        return None
    lg, home, away = card["league"], card["home"], card["away"]
    fh = fd.team(home, lg) if fd is not None and lg in getattr(fd, "rows", {}) else None
    fa = fd.team(away, lg) if fd is not None and lg in getattr(fd, "rows", {}) else None
    out = {}
    for mk, names in (("ah", AH_SIDES), ("ou", OU_SIDES)):
        m = ref.get(mk)
        if not m or not m.get("now") or not m.get("open"):
            continue
        o, n = m["open"], m["now"]
        if not n.get("odds"):
            continue
        d = ah_dir(o["line"], n["line"], o.get("odds"), n["odds"]) if mk == "ah" else ou_dir(o.get("lam"), n.get("lam"))
        move = (f"{_line_txt(mk, o['line'])} → {_line_txt(mk, n['line'])}" if o["line"] != n["line"]
                else f"{_line_txt(mk, n['line'])} 没变")
        mkn = "让球" if mk == "ah" else "大小球"
        items, net = [], 0
        # the same evidence may exist in both sources (own history / football-data): merge the two stats
        def merged(kind):
            st = Stat()
            for src, hn, an in (("own", home, away), ("fd", fh, fa)):
                if kind in ("home_t", "away_t") and not (hn if kind == "home_t" else an):
                    continue
                k = keys_for(mk, src, lg, hn, an, o["line"], n["line"], d)[kind]
                if kind in ("exact", "allx", "lgdir") and src == "fd":
                    continue                              # league-level keys are shared by both sources
                s = ix.get(k)
                if s:
                    st.n += s.n
                    st.w += s.w
                    st.l += s.l
                    st.p += s.p
            return st
        where = {"exact": f"{lg}{mkn}盘口 {move}", "allx": f"所有联赛{mkn}盘口 {move}", "lgdir": f"{lg}{DIR_TXT[mk][d]}时",
                 "home_t": f"{home} 主场、{DIR_TXT[mk][d]}时", "away_t": f"{away} 客场、{DIR_TXT[mk][d]}时"}
        for kind in ("exact", "allx", "lgdir", "home_t", "away_t"):
            v, strong, view = vote(merged(kind), kind)
            if not view or view["n"] < max(5, NMIN[kind] // 2):
                continue
            items.append(_item(kind, mk, v, strong, view, where[kind], names, view["n"] < NMIN[kind]))
            net += v * (2 if strong else 1)
        g = grade(net)
        side = None if not net else (0 if net > 0 else 1)
        pros = sum(1 for it in items if it["vote"] and (it["vote"] > 0) == (net > 0)) if net else 0
        cons = sum(1 for it in items if it["vote"] and (it["vote"] > 0) != (net > 0)) if net else \
            sum(1 for it in items if it["vote"])
        if side is None:
            label = "证据互相矛盾" if cons else ("没有足够的历史样本" if not items else "看不出方向")
        else:
            label = ("略偏", "偏", "明显偏")[g - 1] + names[side]
        res = {"dir": side, "side": names[side] if side is not None else None, "label": label, "grade": g, "net": net,
               "pros": pros, "cons": cons, "move": move, "movedir": DIR_TXT[mk][d],
               "items": sorted(items, key=lambda it: (-abs(it["vote"]) - it["strong"], -it["n"]))}
        if side is not None:
            # the price to take: the best of all books on the current Pinnacle line
            best = (n["odds"][side], "pinnacle")
            for bk, bd in books.items():
                bn = ((bd.get(mk) or {}).get("now") or {})
                if bn.get("line") == n["line"] and bn.get("odds") and bn["odds"][side] > best[0]:
                    best = (bn["odds"][side], bk)
            res["bet"] = {"what": side_line(mk, side, n["line"]), "odds": best[0], "book": best[1], "line": n["line"],
                          "side": ("home", "away")[side] if mk == "ah" else ("over", "under")[side]}
        out[mk] = res
    return out or None
