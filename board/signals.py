"""Turn odds movement + team form into short, readable flags. Every flag says what moved and by how much;
none of them is a betting instruction."""

from __future__ import annotations

from .history import ah_name

SHARP = "pinnacle"


def _lvl(x, cuts):
    return sum(abs(x) >= c for c in cuts)


def _pct(p):
    return f"{p * 100:.0f}%"


def _line(x):
    s = f"{x:+.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("+0", "-0") else s


def devig(prices):
    inv = [1 / p for p in prices]
    t = sum(inv)
    return [x / t for x in inv]


def power_devig(prices):
    """Fair probabilities with the margin weighted towards long shots (p_i = (1/o_i)^k, sum = 1). Proportional
    de-vigging overstates the long shot's chance, which made long shots look like value when they were not."""
    inv = [1 / p for p in prices]
    lo, hi = 0.5, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(v ** k for v in inv) > 1:
            lo = k
        else:
            hi = k
    ps = [v ** ((lo + hi) / 2) for v in inv]
    t = sum(ps)
    return [x / t for x in ps]


def flags(fx):
    out = []
    books = fx.get("books") or {}
    ref = books.get(SHARP) or (next(iter(books.values())) if books else None)
    if not ref:
        return out
    ou, ah, x2 = ref.get("ou"), ref.get("ah"), ref.get("1x2")
    if ou:
        o, n = ou["open"], ou["now"]
        if o.get("lam") and n.get("lam"):
            d = n["lam"] - o["lam"]
            lv = _lvl(d, (0.12, 0.25, 0.4))
            if lv:
                out.append({"k": "ou", "lv": lv, "dir": "up" if d > 0 else "down",
                            "t": f"大小球：市场预期进球 {o['lam']:.2f} → {n['lam']:.2f}（{d:+.2f}），"
                                 f"{'偏向大球' if d > 0 else '偏向小球'}"})
        elif n["line"] != o["line"]:
            d = n["line"] - o["line"]
            out.append({"k": "ou", "lv": _lvl(d, (0.25, 0.5, 0.75)) or 1, "dir": "up" if d > 0 else "down",
                        "t": f"大小球：盘口线 {o['line']} → {n['line']}"})
    if ah:
        o, n = ah["open"], ah["now"]
        d = n["sup"] - o["sup"]
        lv = _lvl(d, (0.12, 0.25, 0.4))
        if lv:
            who = fx["home"] if d > 0 else fx["away"]
            out.append({"k": "ah", "lv": lv, "dir": "home" if d > 0 else "away",
                        "t": f"亚盘：{ah_name(o['line'])} → {ah_name(n['line'])}（主队净胜球预期 {o['sup']:+.2f} → "
                             f"{n['sup']:+.2f}），资金流向 {who}"})
    if x2:
        o, n = x2["open"]["p"], x2["now"]["p"]
        names = ("主胜", "平局", "客胜")
        i = max(range(3), key=lambda k: abs(n[k] - o[k]))
        d = n[i] - o[i]
        lv = _lvl(d, (0.04, 0.07, 0.10))
        if lv:
            out.append({"k": "1x2", "lv": lv, "dir": ("home", "draw", "away")[i],
                        "t": f"胜平负：{names[i]} {_pct(o[i])} → {_pct(n[i])}"})
    # best price among the other books vs Pinnacle's fair price. Backtest (football-data 2017-2026, best market
    # price at close): >= 4% above fair won +5.4% / +5.1% (1x2, power de-vig), +4.9% / +3.4% (Asian handicap),
    # +3.1% / +13.2% (over/under 2.5) in the discovery / validation periods; 2-4% about half that.
    if SHARP in books:
        sh = books[SHARP]
        cands = []
        rx = (sh.get("1x2") or {}).get("now")
        if rx and rx.get("odds"):
            fair = power_devig(rx["odds"])
            for i, nm in enumerate(("主胜", "平局", "客胜")):
                cands.append(("1x2", ("home", "draw", "away")[i], None, fair[i], f"入手{nm}", nm,
                              lambda d, i=i: ((d.get("1x2") or {}).get("now") or {}).get("odds", [None] * 3)[i]))
        for mk, sides, labs in (("ou", ("over", "under"), ("大", "小")), ("ah", ("home", "away"), ("主队", "客队"))):
            r = (sh.get(mk) or {}).get("now")
            if not r or not r.get("odds"):
                continue
            fair = devig(r["odds"])
            for i in (0, 1):
                ln = r["line"]
                bet = f"买{labs[i]} {ln:g}" if mk == "ou" else f"买{labs[i]}（{ah_name(ln) if i == 0 else ah_name(-ln).replace('主', '客')}）"

                def get(d, mk=mk, i=i, ln=ln):
                    x = (d.get(mk) or {}).get("now")
                    return x["odds"][i] if x and x.get("odds") and x["line"] == ln else None
                cands.append((mk, sides[i], ln, fair[i], bet, labs[i], get))
        for mk, side, ln, fair, bet, nm, get in cands:
            best = max(((get(d), bk) for bk, d in books.items() if bk != SHARP and get(d)), default=None)
            if not best:
                continue
            price, bk = best
            edge = price * fair - 1
            if edge >= 0.02:
                strong = edge >= 0.04
                out.append({"k": "value", "lv": 3 if strong else 1, "dir": nm, "ev": round(edge, 4), "bet": bet,
                            "book": bk, "odds": price, "mk": mk, "side": side, "line": ln,
                            "t": f"价格：{bk} {bet} 赔率 {price:.2f}，比 Pinnacle 公平价高 {edge * 100:.1f}%"
                                 + ("（回测：高 4% 以上长期约 +3%~+5%）" if strong else "（回测：高 2%~4% 优势很小）")})
    # form vs market
    et = fx.get("exp_total")
    if et and ou and ou["now"].get("lam"):
        lam = ou["now"]["lam"]
        gap = et - lam
        if abs(gap) >= 0.5:
            out.append({"k": "form", "lv": 1 if abs(gap) < 0.8 else 2, "dir": "up" if gap > 0 else "down",
                        "t": f"基本面：近期场均推算 {et:.2f} 球，市场预期 {lam:.2f} 球（{'基本面偏大' if gap > 0 else '基本面偏小'}）"})
        mv = lam - ou["open"]["lam"] if ou["open"].get("lam") else 0
        if gap >= 0.3 and mv <= -0.15:
            out.append({"k": "conflict", "lv": 2, "dir": "down", "t": "注意：基本面偏大，但盘口在往小球方向走"})
        elif gap <= -0.3 and mv >= 0.15:
            out.append({"k": "conflict", "lv": 2, "dir": "up", "t": "注意：基本面偏小，但盘口在往大球方向走"})
    out.sort(key=lambda f: -f["lv"])
    return out
