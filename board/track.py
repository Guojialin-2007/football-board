"""信号战绩: every signal the board shows becomes a pretend 1-unit bet in the signal's direction.

Live: recorded before kickoff on every run (first time seen + the last snapshot before kickoff), settled after the
match from the final score, Asian-handicap rules, at the recorded odds. Backtest: the same rules replayed on the
board's own history (Pinnacle opening vs closing, closing odds), so there are numbers from day one.
Writes data/track.json (state) and site/track.json (summary for the page)."""

from __future__ import annotations

import json
import os
import time

from . import history as H

LAM_CUTS = (0.12, 0.25, 0.4)       # same thresholds as board/signals.py
X2_CUTS = (0.04, 0.07, 0.10)
LV = {1: "小幅", 2: "中幅", 3: "大幅"}

LABELS = {
    "ou_follow": "大小球预期进球变动：跟着买",
    "ou_fade": "大小球预期进球变动：反着买（诱盘假设）",
    "ah_follow": "亚盘让球强度变动：跟着买",
    "ah_fade": "亚盘让球强度变动：反着买（诱盘假设）",
    "x2_follow": "胜平负概率变动最大的一项：跟着买",
    "ouline_follow": "大小球盘口线升降档：跟着买",
    "ouline_fade": "大小球盘口线升降档：反着买（诱盘假设）",
    "ahline_follow": "亚盘盘口升降档：跟着买",
    "ahline_fade": "亚盘盘口升降档：反着买（诱盘假设）",
    "value": "某家庄赔率高过 Pinnacle 公平价",
    "form": "基本面和市场预期差 0.5 球以上：按基本面买",
    "conflict": "基本面和盘口方向矛盾：跟盘口买",
    "reco": "默认推荐（综合期望 ≥4%，按当时最好的价格）",
}


def _lvl(x, cuts):
    return sum(abs(x) >= c for c in cuts)


def bets_from(st):
    """st: {"ou": {ol, cl, olam, clam, odds}, "ah": {ol, cl, osup, csup, odds}, "x2": {op, cp, odds}} (Pinnacle,
    open vs now/close). -> {key: bet}; bet = {mk, side, line, odds, lv, g} where g is the group (key|level)."""
    out = {}

    def put(fam, lv, mk, side, line, odds):
        if odds:
            out[fam] = {"mk": mk, "side": side, "line": line, "odds": round(odds, 3), "lv": lv, "g": f"{fam}|{lv}"}

    ou, ah, x2 = st.get("ou"), st.get("ah"), st.get("x2")
    if ou and ou.get("odds"):
        o, u = ou["odds"]
        if ou.get("olam") and ou.get("clam"):
            d = ou["clam"] - ou["olam"]
            lv = _lvl(d, LAM_CUTS)
            if lv:
                put("ou_follow", lv, "ou", "over" if d > 0 else "under", ou["cl"], o if d > 0 else u)
                put("ou_fade", lv, "ou", "under" if d > 0 else "over", ou["cl"], u if d > 0 else o)
        k = H.steps(ou["ol"], ou["cl"])
        if k:
            lv = min(abs(k), 3)
            put("ouline_follow", lv, "ou", "over" if k > 0 else "under", ou["cl"], o if k > 0 else u)
            put("ouline_fade", lv, "ou", "under" if k > 0 else "over", ou["cl"], u if k > 0 else o)
    if ah and ah.get("odds"):
        h, a = ah["odds"]
        if ah.get("osup") is not None and ah.get("csup") is not None:
            d = ah["csup"] - ah["osup"]
            lv = _lvl(d, LAM_CUTS)
            if lv:
                put("ah_follow", lv, "ah", "home" if d > 0 else "away", ah["cl"], h if d > 0 else a)
                put("ah_fade", lv, "ah", "away" if d > 0 else "home", ah["cl"], a if d > 0 else h)
        k = -H.steps(ah["ol"], ah["cl"])                       # + = towards the home side
        if k:
            lv = min(abs(k), 3)
            put("ahline_follow", lv, "ah", "home" if k > 0 else "away", ah["cl"], h if k > 0 else a)
            put("ahline_fade", lv, "ah", "away" if k > 0 else "home", ah["cl"], a if k > 0 else h)
    if x2 and x2.get("odds") and x2.get("op") and x2.get("cp"):
        i = max(range(3), key=lambda j: abs(x2["cp"][j] - x2["op"][j]))
        lv = _lvl(x2["cp"][i] - x2["op"][i], X2_CUTS)
        if lv and x2["cp"][i] > x2["op"][i]:                  # only an outcome that got MORE likely is a bet
            put("x2_follow", lv, "1x2", ("home", "draw", "away")[i], None, x2["odds"][i])
    return out


def state_from_card(ref):
    st = {}
    if ref.get("ou"):
        o, n = ref["ou"]["open"], ref["ou"]["now"]
        st["ou"] = {"ol": o["line"], "cl": n["line"], "olam": o.get("lam"), "clam": n.get("lam"), "odds": n.get("odds")}
    if ref.get("ah"):
        o, n = ref["ah"]["open"], ref["ah"]["now"]
        st["ah"] = {"ol": o["line"], "cl": n["line"], "osup": o.get("sup"), "csup": n.get("sup"), "odds": n.get("odds")}
    if ref.get("1x2"):
        st["x2"] = {"op": ref["1x2"]["open"]["p"], "cp": ref["1x2"]["now"]["p"], "odds": ref["1x2"]["now"]["odds"]}
    return st


def state_from_row(r):
    st = {}
    if r.get("ou"):
        m = r["ou"]
        st["ou"] = {"ol": m["ol"], "cl": m["cl"], "olam": m.get("olam"), "clam": m.get("clam"), "odds": m.get("codds")}
    if r.get("ah"):
        m = r["ah"]
        st["ah"] = {"ol": m["ol"], "cl": m["cl"], "osup": m.get("osup"), "csup": m.get("csup"), "odds": m.get("codds")}
    if r.get("x2"):
        st["x2"] = {"op": r["x2"]["op"], "cp": r["x2"]["cp"], "odds": r["x2"]["codds"]}
    return st


def extra_bets(fx, ref):
    """Signals that only exist live: soft-book value, form vs market, form/market conflict, the default pick."""
    out = {}
    rc = fx.get("reco")
    if rc and rc.get("go"):
        k = rc["pick_i"]
        out["reco"] = {"mk": "1x2", "side": ("home", "draw", "away")[k], "line": None, "odds": rc["best"][k][0],
                       "book": rc["best"][k][1], "lv": 1, "g": "reco|1"}
    ou = (ref or {}).get("ou")
    for f in fx.get("flags") or []:
        k = f.get("k")
        if k == "value" and f.get("odds") and f.get("mk"):
            key = f"value_{f['book']}_{f['mk']}_{f['side']}"
            out[key] = {"mk": f["mk"], "side": f["side"], "line": f.get("line"), "odds": f["odds"], "lv": f.get("lv", 1),
                        "g": f"value|{f.get('lv', 1)}", "book": f["book"]}
        elif k in ("form", "conflict") and ou and ou["now"].get("odds"):
            over = f["dir"] == "up"
            out[k] = {"mk": "ou", "side": "over" if over else "under", "line": ou["now"]["line"],
                      "odds": ou["now"]["odds"][0 if over else 1], "lv": f.get("lv", 1), "g": f"{k}|{f.get('lv', 1)}"}
    return out


def settle(b, hs, as_):
    diff, goals = hs - as_, hs + as_
    if b["mk"] == "ou":
        return (H.settle_over if b["side"] == "over" else H.settle_under)(goals, b["line"], b["odds"])
    if b["mk"] == "ah":
        return (H.settle_home if b["side"] == "home" else H.settle_away)(diff, b["line"], b["odds"])
    res = "home" if diff > 0 else "draw" if diff == 0 else "away"
    return b["odds"] - 1 if b["side"] == res else -1.0


def _stats(rets):
    n = len(rets)
    if not n:
        return {"n": 0}
    mu = sum(rets) / n
    out = {"n": n, "win": round(sum(r > 0 for r in rets) / n, 3), "push": round(sum(r == 0 for r in rets) / n, 3),
           "roi": round(mu, 4)}
    if n >= 2:
        sd = (sum((r - mu) ** 2 for r in rets) / (n - 1)) ** 0.5
        h = 1.96 * sd / n ** 0.5
        out["ci"] = [round(mu - h, 3), round(mu + h, 3)]
    return out


def _group(items):
    """items: [(group 'fam|lv', ret)] -> {fam: {"all": stats, "lv": {lv: stats}}}"""
    g = {}
    for grp, r in items:
        fam, lv = grp.split("|")
        g.setdefault(fam, {}).setdefault(lv, []).append(r)
    return {fam: {"label": LABELS.get(fam, fam), "all": _stats([r for v in lvs.values() for r in v]),
                  "lv": {lv: _stats(v) for lv, v in sorted(lvs.items())}} for fam, lvs in g.items()}


def backtest(rows):
    items = []
    for r in rows:
        for b in bets_from(state_from_row(r)).values():
            items.append((b["g"], settle(b, r["hs"], r["as"])))
    return _group(items), len(rows)


def record(cards, data_dir, now=None):
    """Snapshot every signal of every match not yet started."""
    now = now or time.time()
    p = os.path.join(data_dir, "track.json")
    st = json.load(open(p)) if os.path.exists(p) else {}
    for c in cards:
        if not c.get("start") or c["start"] <= now:
            continue
        ref = (c.get("books") or {}).get("pinnacle")
        if not ref:
            continue
        bets = bets_from(state_from_card(ref))
        bets.update(extra_bets(c, ref))
        e = st.setdefault(c["id"], {"start": c["start"], "home": c["home"], "away": c["away"], "league": c["league"],
                                    "bets": {}})
        e["start"] = c["start"]
        for key, b in bets.items():
            b = dict(b, t=int(now))
            slot = e["bets"].setdefault(key, {"first": b})
            slot["last"] = b
        # a signal that disappeared before kickoff is no longer shown: stop counting it
        for key in list(e["bets"]):
            if key not in bets and "res" not in e:
                e["bets"][key]["gone"] = True
            elif key in bets:
                e["bets"][key].pop("gone", None)
    json.dump(st, open(p, "w"), ensure_ascii=False)
    return st


def settle_all(data_dir, score_of, now=None, log=print):
    """score_of(entry) -> (hs, as) or None. Settles matches that ended; gives up after 10 days."""
    now = now or time.time()
    p = os.path.join(data_dir, "track.json")
    st = json.load(open(p)) if os.path.exists(p) else {}
    done = 0
    for fid, e in st.items():
        if "res" in e or e["start"] > now - 3 * 3600:
            continue
        sc = score_of(dict(e, match=fid))
        if sc:
            e["res"] = list(sc)
            done += 1
        elif e["start"] < now - 10 * 86400:
            e["res"] = None                       # no score found: dropped from the record
    json.dump(st, open(p, "w"), ensure_ascii=False)
    log(f"[track] {done} matches settled; {sum(1 for e in st.values() if 'res' not in e)} waiting")
    return st


def summary(st, rows, site_dir):
    live_last, live_first = [], []
    for e in st.values():
        if not e.get("res"):
            continue
        hs, as_ = e["res"]
        for b in e["bets"].values():
            if b.get("gone"):
                continue
            live_last.append((b["last"]["g"], settle(b["last"], hs, as_)))
            live_first.append((b["first"]["g"], settle(b["first"], hs, as_)))
    bt, nbt = backtest(rows)
    out = {"generated": int(time.time()), "bt_matches": nbt, "bt": bt, "live": _group(live_last),
           "live_first": _group(live_first), "live_matches": sum(1 for e in st.values() if e.get("res")),
           "since": min((e["start"] for e in st.values()), default=None), "labels": LABELS, "lv_names": LV}
    json.dump(out, open(os.path.join(site_dir, "track.json"), "w"), ensure_ascii=False)
    return out


def attach(cards, summ):
    """Put each signal's track record next to the flag on the card."""
    fam_of = {"ou": "ou_follow", "ah": "ah_follow", "1x2": "x2_follow", "value": "value", "form": "form",
              "conflict": "conflict"}
    for c in cards:
        for f in c.get("flags") or []:
            fam = fam_of.get(f.get("k"))
            if f.get("k") == "hist":
                fam = "ouline_follow" if f.get("dir") == "ou" else "ahline_follow"
            if not fam:
                continue
            rec = {}
            for src in ("bt", "live"):
                x = (summ.get(src) or {}).get(fam)
                if x and x["all"]["n"]:
                    s = x["lv"].get(str(f.get("lv"))) if f.get("k") in ("ou", "ah", "1x2") else None
                    s = s if s and s["n"] else x["all"]
                    rec[src] = {"n": s["n"], "roi": s["roi"], "ci": s.get("ci")}
            if rec:
                f["rec"] = rec
