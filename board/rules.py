"""规律库: classic line-reading rules (升盘降水, 浅盘, 热门被追 ...) as code, backtested on football-data
(Pinnacle early-week vs closing prices, 2019+ for Asian handicap) with a discovery period and a later
validation period, then matched against each match on the board.

Every rule says which side it is about relative to the match (上盘/下盘, 大/小, 热门/平/冷门); the verdict is
written from the numbers: 有效 (both periods win and the pooled interval is above 0), 值得盯 (both periods win
but the interval still crosses 0), 避坑 (both periods lose clearly more than the margin), else 没有优势."""

from __future__ import annotations

import statistics as stx
from datetime import datetime, timezone

from . import history as H
from . import odds as O

SPLIT = datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp()
W = 0.05                      # water (decimal odds) change that counts as 升水 / 降水

RULES = {
    # Asian handicap, 上盘 = the side giving goals at the current line
    "升盘降水": ("ah", "盘口往上盘方向升，同时上盘水位降", "实盘，看好上盘"),
    "升盘升水": ("ah", "盘口往上盘方向升，但上盘水位也升", "诱上盘，买下盘"),
    "升盘水位不变": ("ah", "盘口升，水位基本没动", ""),
    "降盘降水": ("ah", "盘口往下盘方向降，同时上盘水位降", "庄家降盘诱下盘，看好上盘"),
    "降盘升水": ("ah", "盘口降，同时上盘水位升", "不看好上盘，买下盘"),
    "降盘水位不变": ("ah", "盘口降，水位基本没动", ""),
    "盘口不变上盘降水": ("ah", "盘口没变，上盘水位降", "资金买上盘"),
    "盘口不变上盘升水": ("ah", "盘口没变，上盘水位升", "资金买下盘"),
    "浅盘": ("ah", "让球比胜平负赔率对应的常见盘口少 0.25 球以上", "浅盘诱上，买下盘"),
    "深盘": ("ah", "让球比胜平负赔率对应的常见盘口多 0.25 球以上", "深盘阻上，看好上盘"),
    "上盘高水": ("ah", "上盘赔率 ≥ 2.00", "高水阻上，看好上盘"),
    "上盘低水": ("ah", "上盘赔率 ≤ 1.80", "低水诱上，买下盘"),
    "升盘 2 档以上": ("ah", "盘口往上盘方向升 0.5 球以上", ""),
    "降盘 2 档以上": ("ah", "盘口往下盘方向降 0.5 球以上", ""),
    "让球方互换": ("ah", "开盘和现在让球的一方换了", ""),
    # over/under (same line at open and now)
    "大球降水": ("ou", "大小球盘口没变，大球概率升 4% 以上", "诱大，买小"),
    "大球升水": ("ou", "大小球盘口没变，大球概率降 4% 以上", "诱小，买大"),
    # 1x2, relative to the favourite
    "超级热门": ("x2", "热门胜率 ≥ 70%", "冷门偏差：别买冷门"),
    "热门被资金追": ("x2", "热门胜率比开盘升 5% 以上", "跟资金买热门"),
    "热门被抛": ("x2", "热门胜率比开盘降 5% 以上", "爆冷信号，买冷门"),
    "平赔明显降": ("x2", "平局概率比开盘升 3% 以上", "防平"),
    "势均力敌且平赔低": ("x2", "两队胜率差 < 10% 且平局赔率 ≤ 3.00", "买平"),
}
SIDES = {"ah": ("上盘", "下盘"), "ou": ("大", "小"), "x2": ("热门", "平", "冷门")}


def _pbin(p):
    return round(p * 20) / 20


def ah_rules(lo, lc, oo, oc, p_home_1x2, med):
    """lo/lc: home line open/now; oo/oc: [home, away] odds open/now. -> (upper side 'h'|'a', [rule names])"""
    if lc < 0:
        U = "h"
    elif lc > 0:
        U = "a"
    else:
        U = "h" if p_home_1x2 >= 0.5 else "a"
    give_o, give_c = (-lo, -lc) if U == "h" else (lo, lc)
    i = 0 if U == "h" else 1
    dl, dw = give_c - give_o, oc[i] - oo[i]
    up, down, wd, wu = dl > 1e-9, dl < -1e-9, dw <= -W, dw >= W
    f = []
    if up:
        f.append("升盘降水" if wd else "升盘升水" if wu else "升盘水位不变")
    elif down:
        f.append("降盘降水" if wd else "降盘升水" if wu else "降盘水位不变")
    elif wd:
        f.append("盘口不变上盘降水")
    elif wu:
        f.append("盘口不变上盘升水")
    m = med.get(_pbin(p_home_1x2)) if med else None
    if m is not None:
        gap = (lc - m) if U == "h" else (m - lc)          # > 0: the upper side gives fewer goals than usual
        if gap >= 0.25 - 1e-9:
            f.append("浅盘")
        elif gap <= -0.25 + 1e-9:
            f.append("深盘")
    if oc[i] >= 2.0:
        f.append("上盘高水")
    elif oc[i] <= 1.80:
        f.append("上盘低水")
    if dl >= 0.5 - 1e-9:
        f.append("升盘 2 档以上")
    if dl <= -0.5 + 1e-9:
        f.append("降盘 2 档以上")
    if lo * lc < 0:
        f.append("让球方互换")
    return U, f


def ou_rules(lo, lc, po, pc):
    if lo != lc:
        return []
    d = pc - po
    return ["大球降水"] if d >= 0.04 else ["大球升水"] if d <= -0.04 else []


def x2_rules(po, pc, odds_c):
    fav = 0 if pc[0] >= pc[2] else 2
    f = []
    if pc[fav] >= 0.70:
        f.append("超级热门")
    d = pc[fav] - po[fav]
    if d >= 0.05:
        f.append("热门被资金追")
    elif d <= -0.05:
        f.append("热门被抛")
    if pc[1] - po[1] >= 0.03:
        f.append("平赔明显降")
    if abs(pc[0] - pc[2]) < 0.10 and odds_c[1] <= 3.0:
        f.append("势均力敌且平赔低")
    return fav, f


def _stat(v):
    n = len(v)
    if n < 2:
        return {"n": n}
    mu = sum(v) / n
    sd = (sum((x - mu) ** 2 for x in v) / (n - 1)) ** 0.5
    h = 1.96 * sd / n ** 0.5
    return {"n": n, "roi": round(mu, 4), "ci": [round(mu - h, 3), round(mu + h, 3)]}


def _verdict(sides, base):
    """sides: {side: {"dev","val","all"}} -> (kind, side, text)"""
    best = None
    for s, x in sides.items():
        d, v, a = x["dev"], x["val"], x["all"]
        if d.get("n", 0) < 80 or v.get("n", 0) < 40:
            continue
        if d["roi"] >= 0.02 and v["roi"] >= 0.02:
            kind = "有效" if a["ci"][0] > 0 else "值得盯"
            cand = (2 if kind == "有效" else 1, a["roi"], kind, s,
                    f"买{s}：两个时期都赚（{d['roi'] * 100:+.1f}%、{v['roi'] * 100:+.1f}%）"
                    + ("" if kind == "有效" else "，但样本还不够，区间跨过 0"))
            best = max(best, cand) if best else cand
    bad = []
    for s, x in sides.items():
        d, v = x["dev"], x["val"]
        if d.get("ci") and v.get("ci") and d["ci"][1] < base and v["ci"][1] < base:
            bad.append(f"别买{s}（两个时期都亏 {d['roi'] * 100:.0f}%、{v['roi'] * 100:.0f}%）")
    if best:
        return best[2], best[3], "；".join([best[4]] + bad)
    if bad:
        return "避坑", None, "；".join(bad)
    return "没有优势", None, "两边的回报都和庄家抽水（约 −2% ~ −3%）差不多"


def backtest(fd):
    rows = [r for v in fd.rows.values() for r in v]
    ahr = [r for r in rows if r.get("ah") is not None and r.get("ahc") is not None and r.get("aho") and r.get("ahco")
           and r.get("c")]
    med = {}
    for r in ahr:
        med.setdefault(_pbin(O.devig(r["c"])[0]), []).append(r["ahc"])
    med = {k: stx.median(v) for k, v in med.items() if len(v) >= 50}
    acc = {}

    def add(rule, per, rets):
        g = acc.setdefault(rule, {})
        for s, x in rets.items():
            g.setdefault(s, {"dev": [], "val": []})[per].append(x)
    for r in ahr:
        per = "dev" if r["d"] < SPLIT else "val"
        U, fs = ah_rules(r["ah"], r["ahc"], r["aho"], r["ahco"], O.devig(r["c"])[0], med)
        diff = r["hg"] - r["ag"]
        hr, ar = H.settle_home(diff, r["ahc"], r["ahco"][0]), H.settle_away(diff, r["ahc"], r["ahco"][1])
        rets = {"上盘": hr if U == "h" else ar, "下盘": ar if U == "h" else hr}
        for f in fs + ["_ah"]:
            add(f, per, rets)
    for r in rows:
        per = "dev" if r["d"] < SPLIT else "val"
        g = r["hg"] + r["ag"]
        if r.get("ou") and r.get("ouc"):
            rets = {"大": r["ouc"][0] - 1 if g > 2.5 else -1.0, "小": r["ouc"][1] - 1 if g < 2.5 else -1.0}
            for f in ou_rules(2.5, 2.5, O.devig(r["ou"])[0], O.devig(r["ouc"])[0]) + ["_ou"]:
                add(f, per, rets)
        if r.get("o") and r.get("c"):
            fav, fs = x2_rules(O.devig(r["o"]), O.devig(r["c"]), r["c"])
            res = 0 if r["hg"] > r["ag"] else 1 if r["hg"] == r["ag"] else 2
            dog = 2 - fav
            pay = lambda i: r["c"][i] - 1 if res == i else -1.0
            rets = {"热门": pay(fav), "平": pay(1), "冷门": pay(dog)}
            for f in fs + ["_x2"]:
                add(f, per, rets)
    out = {"med": med, "rules": {}, "base": {}}
    for rule, sides in sorted(acc.items(), key=lambda kv: not kv[0].startswith("_")):
        st = {s: {"dev": _stat(x["dev"]), "val": _stat(x["val"]), "all": _stat(x["dev"] + x["val"])} for s, x in sides.items()}
        if rule.startswith("_"):
            out["base"][rule[1:]] = st
            continue
        mk, what, claim = RULES[rule]
        kind, side, text = _verdict(st, -0.03)            # "避坑" = clearly worse than the ~3% margin
        out["rules"][rule] = {"market": mk, "what": what, "claim": claim, "sides": st, "verdict": kind,
                              "side": side, "text": text}
    return out


def for_match(ref, lib):
    """Rules that fire for this match now (Pinnacle open vs now), with their backtested verdict."""
    if not ref or not lib:
        return []
    hits = []
    x = ref.get("1x2")
    if ref.get("ah") and x:
        a = ref["ah"]
        if a["open"].get("odds") and a["now"].get("odds"):
            U, fs = ah_rules(a["open"]["line"], a["now"]["line"], a["open"]["odds"], a["now"]["odds"], x["now"]["p"][0],
                             {float(k): v for k, v in lib["med"].items()})
            names = {"上盘": "主队" if U == "h" else "客队", "下盘": "客队" if U == "h" else "主队"}
            hits += [(f, names) for f in fs]
    if ref.get("ou") and ref["ou"]["open"].get("odds") and ref["ou"]["now"].get("odds"):
        o = ref["ou"]
        hits += [(f, {"大": f"大 {o['now']['line']:g}", "小": f"小 {o['now']['line']:g}"})
                 for f in ou_rules(o["open"]["line"], o["now"]["line"], O.devig(o["open"]["odds"])[0],
                                   O.devig(o["now"]["odds"])[0])]
    if x and x["open"].get("odds") and x["now"].get("odds"):
        fav, fs = x2_rules(x["open"]["p"], x["now"]["p"], x["now"]["odds"])
        names = {"热门": "主胜" if fav == 0 else "客胜", "平": "平局", "冷门": "客胜" if fav == 0 else "主胜"}
        hits += [(f, names) for f in fs]
    out = []
    for f, names in hits:
        r = lib["rules"].get(f)
        if not r:
            continue
        text = r["text"]
        for s, nm in names.items():
            text = text.replace(f"买{s}", f"买{s}（{nm}）")
        out.append({"rule": f, "what": r["what"], "claim": r["claim"], "verdict": r["verdict"], "text": text,
                    "n": max(x["all"].get("n", 0) for x in r["sides"].values())})
    order = {"有效": 0, "值得盯": 1, "避坑": 2, "没有优势": 3}
    return sorted(out, key=lambda h: order[h["verdict"]])
