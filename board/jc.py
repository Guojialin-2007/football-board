"""One score distribution, consistent with the sharp market, and every 竞彩 outcome derived from it.

Fit:  home / away goal rates (lh, la) and a Dixon-Coles low-score correction (rho) so that the model reproduces
Pinnacle's de-vigged prices for the main Asian-handicap line, the main over/under line and the 1x2 draw.
Quarter lines are matched the way they settle (a half win counts as half a win), so the fit is exact for any line.

Then:  P(home i, away j) for all scores up to MAXG, the first-half split from the share of goals scored before
half-time (calibrated on football-data), and from those the 54 竞彩 outcomes:
  胜平负 3, 让球胜平负 3 (integer handicap, official settlement), 总进球 8 (0-6, 7+), 比分 31 (with 胜其他/平其他/负其他),
  半全场 9.
Consistency checks run on every result (each market sums to 1, 胜 = 让胜 + 让平 for handicap -1, ...)."""

from __future__ import annotations

import math

MAXG = 10
FIRST_HALF_SHARE = 0.42          # best half-time/full-time log loss on 3,000 football-data matches (actual share 0.44)

SCORES_H = ["1:0", "2:0", "2:1", "3:0", "3:1", "3:2", "4:0", "4:1", "4:2", "5:0", "5:1", "5:2"]
SCORES_D = ["0:0", "1:1", "2:2", "3:3"]
SCORES_A = ["0:1", "0:2", "1:2", "0:3", "1:3", "2:3", "0:4", "1:4", "2:4", "0:5", "1:5", "2:5"]
HAFU = ["胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负"]


def _pois(lam, n=MAXG):
    p = [math.exp(-lam)]
    for k in range(1, n + 1):
        p.append(p[-1] * lam / k)
    return p


def grid(lh, la, rho=0.0, n=MAXG):
    """P[i][j], Dixon-Coles corrected, renormalised."""
    ph, pa = _pois(lh, n), _pois(la, n)
    g = [[ph[i] * pa[j] for j in range(n + 1)] for i in range(n + 1)]
    if rho:
        g[0][0] *= 1 - lh * la * rho
        g[0][1] *= 1 + lh * rho
        g[1][0] *= 1 + la * rho
        g[1][1] *= 1 - rho
    t = sum(map(sum, g))
    return [[x / t for x in row] for row in g]


def _ah_home_part(diff, line):
    """Home result on an Asian line at even money: 1 win, .5 half win, 0 push, -.5, -1."""
    q = round(line * 4)
    if q % 2:                                  # quarter line: half on each neighbour
        a, b = (q - 1) / 4, (q + 1) / 4
        return (_ah_home_part(diff, a) + _ah_home_part(diff, b)) / 2
    m = diff + line
    return 1.0 if m > 1e-9 else -1.0 if m < -1e-9 else 0.0


def ah_eff(g, line):
    """P(home cover) the way a de-vigged price means it: weighted wins / (wins + losses), pushes out."""
    w = l = 0.0
    for i, row in enumerate(g):
        for j, p in enumerate(row):
            r = _ah_home_part(i - j, line)
            if r > 0:
                w += p * r
            elif r < 0:
                l += p * -r
    return w / (w + l) if w + l else 0.5


def ou_eff(g, line):
    w = l = 0.0
    for i, row in enumerate(g):
        for j, p in enumerate(row):
            r = _ah_home_part(i + j, -line)    # over = total - line > 0
            if r > 0:
                w += p * r
            elif r < 0:
                l += p * -r
    return w / (w + l) if w + l else 0.5


def p1x2(g):
    h = d = a = 0.0
    for i, row in enumerate(g):
        for j, p in enumerate(row):
            if i > j:
                h += p
            elif i == j:
                d += p
            else:
                a += p
    return h, d, a


def fit(ah_line=None, p_ah=None, ou_line=None, p_over=None, p_draw=None, p_home=None):
    """Find lh, la, rho matching the market. Needs a total (ou) and a split (ah, or the 1x2 home probability).
    rho is fitted only when the draw price is given."""
    def loss(lh, la, rho):
        g = grid(lh, la, rho)
        e = 0.0
        if ou_line is not None:
            e += (ou_eff(g, ou_line) - p_over) ** 2
        if ah_line is not None:
            e += (ah_eff(g, ah_line) - p_ah) ** 2
        elif p_home is not None:
            e += (p1x2(g)[0] - p_home) ** 2
        if p_draw is not None:
            e += (p1x2(g)[1] - p_draw) ** 2
        return e
    # coarse-to-fine coordinate search (smooth, 2-3 parameters: fast enough and robust)
    lh, la, rho = 1.4, 1.1, 0.0
    step = [0.4, 0.4, 0.08 if p_draw is not None else 0.0]
    best = loss(lh, la, rho)
    for _ in range(80):
        improved = False
        for k in range(3):
            if not step[k]:
                continue
            for sgn in (1, -1):
                cand = [lh, la, rho]
                cand[k] += sgn * step[k]
                if cand[0] < 0.05 or cand[1] < 0.05 or abs(cand[2]) > 0.3:
                    continue
                e = loss(*cand)
                if e < best:
                    best, (lh, la, rho), improved = e, cand, True
        if not improved:
            step = [s / 2 for s in step]
            if max(step) < 1e-4:
                break
    return {"lh": round(lh, 4), "la": round(la, 4), "rho": round(rho, 4), "err": best}


def halves(lh, la, share=FIRST_HALF_SHARE, n=8):
    """Independent first and second halves (rates split by `share`) -> P(half-time result, full-time result)."""
    g1 = grid(lh * share, la * share, 0.0, n)
    g2 = grid(lh * (1 - share), la * (1 - share), 0.0, n)
    res = lambda d: "胜" if d > 0 else "平" if d == 0 else "负"
    out = {k: 0.0 for k in HAFU}
    for i1, r1 in enumerate(g1):
        for j1, p1 in enumerate(r1):
            if p1 < 1e-12:
                continue
            ht = res(i1 - j1)
            for i2, r2 in enumerate(g2):
                for j2, p2 in enumerate(r2):
                    out[ht + res(i1 + i2 - j1 - j2)] += p1 * p2
    return out


def outcomes(g, handicap=None, hafu=None):
    """All 竞彩 outcomes from a score grid. handicap: the official integer 让球 for the home side (-1 = home gives 1)."""
    h, d, a = p1x2(g)
    o = {"胜平负": {"胜": h, "平": d, "负": a}}
    if handicap is not None:
        rw = rd = rl = 0.0
        for i, row in enumerate(g):
            for j, p in enumerate(row):
                m = i - j + handicap
                if m > 0:
                    rw += p
                elif m == 0:
                    rd += p
                else:
                    rl += p
        o["让球胜平负"] = {"让胜": rw, "让平": rd, "让负": rl, "handicap": handicap}
    tg = [0.0] * 8
    for i, row in enumerate(g):
        for j, p in enumerate(row):
            tg[min(i + j, 7)] += p
    o["总进球"] = {(str(k) if k < 7 else "7+"): tg[k] for k in range(8)}
    cs = {}
    for s in SCORES_H + SCORES_D + SCORES_A:
        i, j = map(int, s.split(":"))
        cs[s] = g[i][j]
    cs["胜其他"] = h - sum(cs[s] for s in SCORES_H)
    cs["平其他"] = d - sum(cs[s] for s in SCORES_D)
    cs["负其他"] = a - sum(cs[s] for s in SCORES_A)
    o["比分"] = cs
    if hafu:                                   # rescale so the full-time column matches this grid (rho included)
        ft = {"胜": h, "平": d, "负": a}
        tot = {r: sum(v for k, v in hafu.items() if k[1] == r) for r in "胜平负"}
        o["半全场"] = {k: v * ft[k[1]] / tot[k[1]] if tot[k[1]] else 0.0 for k, v in hafu.items()}
    return o


def check(o):
    """Internal consistency; returns a list of problems (empty = fine)."""
    bad = []
    for k, v in o.items():
        s = sum(x for kk, x in v.items() if kk != "handicap")
        if abs(s - 1) > 1e-6:
            bad.append(f"{k} 合计 {s:.6f}")
    if "让球胜平负" in o and o["让球胜平负"]["handicap"] == -1:
        if abs(o["胜平负"]["胜"] - (o["让球胜平负"]["让胜"] + o["让球胜平负"]["让平"])) > 1e-9:
            bad.append("让 -1：胜 ≠ 让胜 + 让平")
    if "半全场" in o:
        for ft in "胜平负":
            s = sum(v for k, v in o["半全场"].items() if k[1] == ft)
            if abs(s - o["胜平负"][ft]) > 0.01:
                bad.append(f"半全场的全场{ft} {s:.3f} ≠ 胜平负 {o['胜平负'][ft]:.3f}")
    return bad


def from_market(ah_line=None, ah_odds=None, ou_line=None, ou_odds=None, x2_odds=None, handicap=None, share=None):
    """Market prices (Pinnacle) -> fitted rates + all outcomes. Two-way prices are de-vigged proportionally (their
    margin is small and symmetric); the 1x2 with the power method."""
    from .signals import devig, power_devig
    p_ah = devig(ah_odds)[0] if ah_odds else None
    p_ov = devig(ou_odds)[0] if ou_odds else None
    px = power_devig(x2_odds) if x2_odds else None
    f = fit(ah_line if p_ah is not None else None, p_ah, ou_line if p_ov is not None else None, p_ov,
            px[1] if px else None, px[0] if px else None)
    g = grid(f["lh"], f["la"], f["rho"])
    o = outcomes(g, handicap, halves(f["lh"], f["la"], share or FIRST_HALF_SHARE))
    return {"fit": f, "outcomes": o, "problems": check(o), "grid": [[round(x, 6) for x in row[:9]] for row in g[:9]]}
