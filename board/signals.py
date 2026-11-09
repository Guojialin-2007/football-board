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
    # soft bookmaker paying more than the sharp book's fair price
    for bk, d in books.items():
        if bk == SHARP or SHARP not in books:
            continue
        sx, rx = (d.get("1x2") or {}).get("now"), (books[SHARP].get("1x2") or {}).get("now")
        if sx and rx:
            for i, nm in enumerate(("主胜", "平局", "客胜")):
                edge = sx["odds"][i] * rx["p"][i] - 1
                if edge >= 0.03:
                    out.append({"k": "value", "lv": 1 if edge < 0.06 else 2, "dir": nm, "ev": round(edge, 4),
                                "bet": f"入手{nm}", "book": bk, "odds": sx["odds"][i],
                                "t": f"{bk} {nm} 赔率 {sx['odds'][i]:.2f}，比 Pinnacle 公平价高 {edge * 100:.0f}%"})
        so, ro = (d.get("ou") or {}).get("now"), (books[SHARP].get("ou") or {}).get("now")
        if so and ro and so["line"] == ro["line"]:
            for i, nm in enumerate(("大", "小")):
                fair = ro["p_over"] if i == 0 else 1 - ro["p_over"]
                edge = so["odds"][i] * fair - 1
                if edge >= 0.03:
                    out.append({"k": "value", "lv": 1 if edge < 0.06 else 2, "dir": nm, "ev": round(edge, 4),
                                "bet": f"买{nm} {so['line']:g}", "book": bk, "odds": so["odds"][i],
                                "t": f"{bk} {nm}{so['line']} 赔率 {so['odds'][i]:.2f}，比 Pinnacle 公平价高 {edge * 100:.0f}%"})
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
