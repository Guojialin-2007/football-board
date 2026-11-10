"""默认推荐: every match gets one, from weighted signals.

Weights come from data (board/reco_train.py: multinomial logistic regression on football-data, 20 seasons,
trained before 2022-07 and checked after). Signals: the market (Pinnacle, power de-vig), recent form (points per
game), mean reversion (points above the market's expectation over the last 5), "good form but priced low",
the same team at similar odds never winning lately, and the head-to-head draw share.

Recommendation, 1x2:
  推荐买 X   the weighted probability x the best price on offer (Pinnacle or a soft book) - 1 >= 4%
  不建议下注  otherwise; the least bad option is named with its expectation
Backtest after 2022-07 (closing prices): 2,722 bets at >= 4%, +4.8% (95% interval -1.5% .. +11%): positive, not proven.
"""

from __future__ import annotations

import json
import math
import os
from collections import deque

from .signals import power_devig

W = json.load(open(os.path.join(os.path.dirname(__file__), "reco_weights.json")))
TH = 0.04
OUT = ("主胜", "平局", "客胜")


def state(fd, league, home, away, start):
    """Pre-match signals from football-data rows of this league before `start` (None if the teams are unknown)."""
    rows = [r for r in (fd.rows.get(league) or []) if r["d"] < start and r.get("c") and min(r["c"]) > 1]
    th, ta = fd.team(home, league), fd.team(away, league)
    if not rows or not th or not ta:
        return None
    form = {th: deque(maxlen=5), ta: deque(maxlen=5)}
    same = {(th, "H"): [], (ta, "A"): []}
    h2h = []
    for r in rows:
        res = 0 if r["hg"] > r["ag"] else 1 if r["hg"] == r["ag"] else 2
        p = power_devig(r["c"])
        eh, ea = 3 * p[0] + p[1], 3 * p[2] + p[1]
        ph = 3 if res == 0 else 1 if res == 1 else 0
        pa = 3 if res == 2 else 1 if res == 1 else 0
        if r["h"] in form:
            form[r["h"]].append((ph, ph - eh))
        if r["a"] in form:
            form[r["a"]].append((pa, pa - ea))
        if (r["h"], "H") in same:
            same[(r["h"], "H")].append((r["c"][0], res == 0))
        if (r["a"], "A") in same:
            same[(r["a"], "A")].append((r["c"][2], res == 2))
        if {r["h"], r["a"]} == {th, ta}:
            h2h.append(res)
    if len(form[th]) < 3 or len(form[ta]) < 3:
        return None
    return {"form_h": list(form[th]), "form_a": list(form[ta]), "same_h": same[(th, "H")], "same_a": same[(ta, "A")],
            "h2h": h2h, "last": rows[-1]["d"]}


def features(p, odds, st):
    fh, fa = st["form_h"], st["form_a"]
    ppg_h, ppg_a = sum(t[0] for t in fh) / len(fh), sum(t[0] for t in fa) / len(fa)
    over_h, over_a = sum(t[1] for t in fh) * 5 / len(fh), sum(t[1] for t in fa) * 5 / len(fa)
    sh = [t for t in st["same_h"] if abs(t[0] - odds[0]) <= 0.15][-5:]
    sa = [t for t in st["same_a"] if abs(t[0] - odds[2]) <= 0.15][-5:]
    h2d = (sum(1 for t in st["h2h"] if t == 1) + 0.27 * 3) / (len(st["h2h"]) + 3)
    return [math.log(p[0] / p[2]), math.log(p[1] / (1 - p[1])), ppg_h - ppg_a, over_h / 5, over_a / 5,
            float(ppg_h >= 2.0 and ppg_a <= 1.2 and p[0] <= 0.5), float(ppg_a >= 2.0 and ppg_h <= 1.2 and p[2] <= 0.5),
            float(len(sh) >= 3 and not any(t[1] for t in sh)), float(len(sa) >= 3 and not any(t[1] for t in sa)), h2d]


def predict(x):
    z = [W["intercept"][k] + sum(c * v for c, v in zip(W["coef"][k], x)) for k in range(3)]
    m = max(z)
    e = [math.exp(v - m) for v in z]
    return [v / sum(e) for v in e]


def neutral(x):
    """The same match with every non-market signal switched off (form equal, nothing unusual, average h2h)."""
    return x[:2] + [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.27]


def for_match(card, fd):
    books = card.get("books") or {}
    ref = books.get("pinnacle")
    x2 = (ref or {}).get("1x2", {}).get("now")
    if not x2 or not x2.get("odds"):
        return None
    p_mk = power_devig(x2["odds"])
    st = state(fd, card["league"], card["home"], card["away"], card["start"]) if fd is not None else None
    if st:
        x = features(p_mk, x2["odds"], st)
        p = predict(x)
        base = predict(neutral(x))
        # each signal's own effect: switch it on alone over the neutral match
        names = W["names"]
        contrib = []
        for j in range(2, len(x)):
            xx = neutral(x)
            xx[j] = x[j]
            q = predict(xx)
            d = [q[k] - base[k] for k in range(3)]
            if max(abs(v) for v in d) >= 0.002:
                k = max(range(3), key=lambda i: abs(d[i]))
                contrib.append({"name": names[j], "on": OUT[k], "d": round(d[k], 4)})
        src = "综合（市场 + 状态 + 均值回归 + 同赔 + 交手）"
    else:
        p, contrib, src = predict(features(p_mk, x2["odds"], {"form_h": [(1, 0)] * 3, "form_a": [(1, 0)] * 3,
                                                               "same_h": [], "same_a": [], "h2h": []})), [], "只有市场（缺少两队的历史数据）"
    # best price on offer per outcome
    best = []
    for k in range(3):
        cands = [((b.get("1x2") or {}).get("now") or {}).get("odds") for b in books.values()]
        prices = [(o[k], bk) for o, bk in zip(cands, books) if o and len(o) == 3]
        best.append(max(prices) if prices else (x2["odds"][k], "pinnacle"))
    ev = [p[k] * best[k][0] - 1 for k in range(3)]
    k = max(range(3), key=lambda i: ev[i])
    rec = {"p": [round(v, 4) for v in p], "p_market": [round(v, 4) for v in p_mk], "ev": [round(v, 4) for v in ev],
           "best": [[b[0], b[1]] for b in best], "contrib": contrib, "src": src, "pick": OUT[k], "pick_i": k,
           "go": ev[k] >= TH, "th": TH}
    rec["text"] = (f"推荐买{OUT[k]}（{best[k][1]} {best[k][0]:.2f}，综合概率 {p[k] * 100:.0f}%，期望 {ev[k] * 100:+.1f}%）"
                   if rec["go"] else f"不建议下注（相对最好：{OUT[k]} {best[k][0]:.2f}，期望 {ev[k] * 100:+.1f}%）")
    return rec
