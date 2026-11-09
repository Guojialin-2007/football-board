"""Learning from 思路: a correct idea becomes a reusable rule, and the board then makes its own 思路 with it.

1. When an idea is submitted, the match situation is saved with it (ctx): which way and how far each line moved,
   the water (price) move, when the line last moved, who is favourite, form vs market.
2. A settled idea that WON becomes a pattern: the situation + the pick expressed RELATIVE to it
   (顺着变盘 / 反着变盘 / 买热门 / 买冷门 ...), so it transfers to other matches.
3. Every upcoming match whose situation matches an active pattern gets a 系统思路 with the reason spelled out;
   it is snapshotted until kickoff and settled like a user idea.
4. Each pattern is checked twice: replayed on the board's own finished matches (backtest) and by its live record
   (the user's ideas + the system's). A pattern is switched off when either says it loses.
"""

from __future__ import annotations

import json
import os
import time

from . import detail as D
from . import history as H
from . import ideas as I

MIN_LIVE_N, BAD_LIVE_ROI = 8, -0.10          # switched off after >= 8 live bets losing more than 10% per bet
MIN_BT_N = 60                                # or a backtest of >= 60 matches whose 95% interval is below 0


def _kb(k, toward):
    if not k:
        return "不变"
    return f"{toward[0] if k > 0 else toward[1]} {min(abs(k), 2)} 档{'以上' if abs(k) >= 2 else ''}"


def _fav(line):
    return "主让" if line < 0 else "客让" if line > 0 else "平手"


def _gap(et, lam):
    if et is None or lam is None:
        return None
    g = et - lam
    return "基本面偏大" if g >= 0.3 else "基本面偏小" if g <= -0.3 else "基本面一致"


def _ctx(ah, ou, x2p, et):
    """ah/ou: {ol, cl, oo (open odds), co (now/close odds), lt (hours before kickoff of last move or None)}"""
    c = {}
    if ah:
        k = -H.steps(ah["ol"], ah["cl"])
        c["ah"] = {"k": k, "kb": _kb(k, ("往主队", "往客队")), "water": D.water(D._dp(ah.get("oo"), ah.get("co")), "主队"),
                   "timing": D.timing(ah.get("lt")), "fav": _fav(ah["cl"])}
    if ou:
        k = H.steps(ou["ol"], ou["cl"])
        c["ou"] = {"k": k, "kb": _kb(k, ("升", "降")), "water": D.water(D._dp(ou.get("oo"), ou.get("co")), "大球"),
                   "timing": D.timing(ou.get("lt")), "gap": _gap(et, ou.get("lam"))}
    if x2p:
        c["x2"] = {"fav": "主" if x2p[0] >= x2p[2] else "客"}
    return c


def context(card):
    """Situation of a match on the board now (for a new idea or for applying patterns)."""
    ref = (card.get("books") or {}).get("pinnacle") or next(iter((card.get("books") or {}).values()), None)
    if not ref:
        return {}
    start = card.get("start") or time.time()

    def lt(m):
        t = (m or {}).get("last_move_t")
        return (start - t) / 3600 if t else None
    ah = ou = x2p = None
    if ref.get("ah"):
        a = ref["ah"]
        ah = {"ol": a["open"]["line"], "cl": a["now"]["line"], "oo": a["open"].get("odds"), "co": a["now"].get("odds"),
              "lt": lt(a)}
    if ref.get("ou"):
        o = ref["ou"]
        ou = {"ol": o["open"]["line"], "cl": o["now"]["line"], "oo": o["open"].get("odds"), "co": o["now"].get("odds"),
              "lt": lt(o), "lam": o["now"].get("lam")}
    if ref.get("1x2"):
        x2p = ref["1x2"]["now"]["p"]
    return _ctx(ah, ou, x2p, card.get("exp_total"))


def context_row(r):
    ah = ou = None
    if r.get("ah"):
        m = r["ah"]
        ah = {"ol": m["ol"], "cl": m["cl"], "oo": m.get("oodds"), "co": m.get("codds"), "lt": m.get("lt")}
    if r.get("ou"):
        m = r["ou"]
        ou = {"ol": m["ol"], "cl": m["cl"], "oo": m.get("oodds"), "co": m.get("codds"), "lt": m.get("lt"), "lam": None}
    return _ctx(ah, ou, (r.get("x2") or {}).get("cp"), None)


# ---- the pick, relative to the situation, and back
def rel_pick(market, pick, c):
    if market == "亚盘" and c.get("ah"):
        k, fav = c["ah"]["k"], c["ah"]["fav"]
        home = pick == "主队"
        if k:
            return "顺着变盘" if (home == (k > 0)) else "反着变盘"
        if fav == "平手":
            return "买主队" if home else "买客队"
        return "买让球方" if home == (fav == "主让") else "买受让方"
    if market == "大小球" and c.get("ou"):
        k = c["ou"]["k"]
        over = pick == "大"
        if k:
            return "顺着变盘" if over == (k > 0) else "反着变盘"
        return "买大" if over else "买小"
    if market == "胜平负" and c.get("x2"):
        if pick == "平局":
            return "平局"
        return "热门胜" if (pick == "主胜") == (c["x2"]["fav"] == "主") else "冷门胜"
    return None


def abs_pick(market, rel, c):
    if market == "亚盘":
        k, fav = c["ah"]["k"], c["ah"]["fav"]
        if rel in ("顺着变盘", "反着变盘"):
            home = (k > 0) == (rel == "顺着变盘")
        elif rel in ("买主队", "买客队"):
            home = rel == "买主队"
        else:
            home = (fav == "主让") == (rel == "买让球方")
        return "主队" if home else "客队"
    if market == "大小球":
        k = c["ou"]["k"]
        if rel in ("顺着变盘", "反着变盘"):
            return "大" if (k > 0) == (rel == "顺着变盘") else "小"
        return "大" if rel == "买大" else "小"
    if rel == "平局":
        return "平局"
    home_fav = c["x2"]["fav"] == "主"
    return "主胜" if home_fav == (rel == "热门胜") else "客胜"


FIELDS = {"亚盘": ("ah", ("kb", "water", "timing", "fav")), "大小球": ("ou", ("kb", "water", "timing", "gap")),
          "胜平负": ("ah", ("kb", "fav"))}


def key_of(market, rel, c):
    sec, fs = FIELDS[market]
    if not c.get(sec) or (market == "胜平负" and not c.get("x2")):
        return None
    return "|".join([market] + [str(c[sec].get(f)) for f in fs] + [rel])


def describe(key):
    parts = key.split("|")
    market, rel, vals = parts[0], parts[-1], [v for v in parts[1:-1] if v not in ("None", "盘口没变")]
    kb = vals[0] if vals else ""
    lab = {"亚盘": "亚盘", "大小球": "大小球", "胜平负": "亚盘"}[market]
    sit = [f"{lab}{kb}" if kb != "不变" else f"{lab}没变"] + vals[1:]
    return "，".join(sit) + f" → {market}{rel}"


def matches(key, market, rel, c, wildcard_none=False):
    k2 = key_of(market, rel, c)
    if not k2:
        return False
    if not wildcard_none:
        return k2 == key
    a, b = key.split("|"), k2.split("|")
    return all(x == y or y == "None" for x, y in zip(a, b))       # history rows have no form gap: ignore that field


def _rec(rets):
    n = len(rets)
    if not n:
        return {"n": 0}
    mu = sum(rets) / n
    out = {"n": n, "win": sum(r > 0 for r in rets), "roi": round(mu, 4)}
    if n >= 2:
        sd = (sum((r - mu) ** 2 for r in rets) / (n - 1)) ** 0.5
        out["ci"] = [round(mu - 1.96 * sd / n ** 0.5, 3), round(mu + 1.96 * sd / n ** 0.5, 3)]
    return out


def _line_odds(market, pick, row):
    if market == "亚盘":
        return row["ah"]["cl"], row["ah"]["codds"][0 if pick == "主队" else 1]
    if market == "大小球":
        return row["ou"]["cl"], row["ou"]["codds"][0 if pick == "大" else 1]
    return None, row["x2"]["codds"][{"主胜": 0, "平局": 1, "客胜": 2}[pick]]


def backtest(key, rows, skip=()):
    parts = key.split("|")
    market, rel = parts[0], parts[-1]
    rets = []
    for r in rows:
        if r["id"] in skip:                   # the match the idea came from is not evidence for its own rule
            continue
        c = context_row(r)
        if not matches(key, market, rel, c, wildcard_none=True):
            continue
        need = {"亚盘": "ah", "大小球": "ou", "胜平负": "x2"}[market]
        if not r.get(need):
            continue
        pick = abs_pick(market, rel, c)
        line, odds = _line_odds(market, pick, r)
        s = I.settle({"market": market, "pick": pick, "line": line, "odds": odds}, r["hs"], r["as"])
        if s:
            rets.append(s["ret"])
    return _rec(rets)


def run(cards, rows, result_of, data_dir, site_dir, now=None, log=print):
    now = now or time.time()
    ideas = json.load(open(os.path.join(data_dir, "ideas.json"))) if os.path.exists(os.path.join(data_dir, "ideas.json")) else {}
    sp = os.path.join(data_dir, "sys_ideas.json")
    sysi = json.load(open(sp)) if os.path.exists(sp) else {}
    zp = os.path.join(site_dir, "zh.json")
    zh = json.load(open(zp)) if os.path.exists(zp) else {}
    z = lambda n: zh.get(n, n)
    # 1. patterns from the user's settled ideas
    pats = {}
    for f in ideas.values():
        if f.get("status") != "settled" or not f.get("ctx"):
            continue
        rel = rel_pick(f["market"], f["pick"], f["ctx"])
        key = key_of(f["market"], rel, f["ctx"]) if rel else None
        if not key:
            continue
        p = pats.setdefault(key, {"key": key, "desc": describe(key), "user": [], "from": []})
        p["user"].append(f["ret"])
        if f["ret"] > 0:
            p.setdefault("skip", set()).add(f.get("match"))
            p["from"].append({"num": f.get("num"), "url": f.get("url"), "game": f"{f.get('home')} vs {f.get('away')}", "game_zh": f"{z(f.get('home'))} vs {z(f.get('away'))}",
                              "pick": f["pick"], "why": (f.get("why") or "")[:200], "tags": f.get("tags") or [],
                              "score": f.get("score"), "word": f.get("word")})
    learned = {k: p for k, p in pats.items() if p["from"]}                 # only what was right at least once
    # 2. settle the system's ideas
    for s in sysi.values():
        if s.get("status") == "open" and now > s["start"] + 3 * 3600:
            res = result_of(s)
            if res:
                st = I.settle(s, *res)
                if st:
                    s.update(score=f"{res[0]}-{res[1]}", ret=st["ret"], word=st["word"], status="settled")
            elif now > s["start"] + 10 * 86400:
                s["status"] = "void"
    # 3. judge each pattern: backtest + live record
    for k, p in learned.items():
        sys_rets = [s["ret"] for s in sysi.values() if s.get("pattern") == k and s.get("status") == "settled"]
        p["bt"] = backtest(k, rows, p.pop("skip", set()))
        p["live"] = _rec(p["user"] + sys_rets)
        p["sys"] = _rec(sys_rets)
        bad_live = p["live"]["n"] >= MIN_LIVE_N and p["live"]["roi"] < BAD_LIVE_ROI
        bad_bt = p["bt"]["n"] >= MIN_BT_N and p["bt"].get("ci") and p["bt"]["ci"][1] < 0
        p["status"] = "停用：实战亏损" if bad_live else "停用：历史回测亏损" if bad_bt else "使用中"
    # 4. apply active patterns to upcoming matches
    made = 0
    for c in cards:
        if not c.get("start") or c["start"] <= now:
            continue
        ctx = context(c)
        for k, p in learned.items():
            if p["status"] != "使用中":
                continue
            market, rel = k.split("|")[0], k.split("|")[-1]
            if not matches(k, market, rel, ctx):
                continue
            pick = abs_pick(market, rel, ctx)
            line, odds = I.snapshot(c, market, pick)
            if odds is None:
                continue
            sid = f"{c['id']}|{k}"
            ex = sysi.get(sid)
            if ex and ex.get("status") != "open":
                continue
            src = p["from"][-1]
            bt = p["bt"]
            why = (f"局面和你的思路「{src['game_zh']}：{src['pick']}」一样（{p['desc']}），那次{src.get('word') or '赢'}。"
                   + (f"这条规律历史回测 {bt['n']} 场，每注 {bt['roi'] * 100:+.1f}%。" if bt.get("n") else "历史里还没有同样的局面。")
                   + (f"实战 {p['live']['n']} 次，每注 {p['live']['roi'] * 100:+.1f}%。" if p["live"].get("n") else ""))
            sysi[sid] = {"match": c["id"], "start": c["start"], "home": c["home"], "away": c["away"], "league": c["league"],
                         "market": market, "pick": pick, "line": line, "odds": odds, "pattern": k, "why": why,
                         "status": "open", "at": (ex or {}).get("at") or int(now)}
            made += not ex
    json.dump(sysi, open(sp, "w"), ensure_ascii=False)
    done = [s for s in sysi.values() if s.get("status") == "settled"]
    user_done = [f for f in ideas.values() if f.get("status") == "settled"]
    out = {"generated": int(now), "patterns": sorted(learned.values(), key=lambda p: -p["live"].get("n", 0)),
           "sys": sorted(sysi.values(), key=lambda s: -s["start"])[:300],
           "rec": {"sys": _rec([s["ret"] for s in done]), "user": _rec([f["ret"] for f in user_done])}}
    for p in out["patterns"]:
        p.pop("user", None)
    json.dump(out, open(os.path.join(site_dir, "learn.json"), "w"), ensure_ascii=False)
    log(f"[learn] {len(learned)} learned patterns ({sum(p['status'] == '使用中' for p in learned.values())} active); "
        f"{made} new 系统思路; {len(done)} settled")
    return out
