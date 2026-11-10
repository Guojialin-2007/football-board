"""Frozen 竞彩 plans (GitHub issues titled "[竞彩方案] ..." made by the 竞彩计算 panel): settle them after the
matches with the frozen odds, comment the result on the issue, keep a record (data/jcplans.json) and publish the
summary (site/jcplans.json).

A plan: {"k": pass count, "mult": multiple, "picks": [{match, home, away, start, play, outcome, handicap, odds, p}]}.
Several picks of one match = 复式; every k-subset of matches is expanded, one pick per match per bet, 2 yuan a bet.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import time

import requests

from .ideas import GH

JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def _res(d):
    return "胜" if d > 0 else "平" if d == 0 else "负"


def hit(pick, hs, as_, hh=None, ha=None):
    """True / False, or None when it needs the half-time score and there is none."""
    play, o, d = pick["play"], pick["outcome"], hs - as_
    if play == "胜平负":
        return _res(d) == o
    if play == "让球":
        return "让" + _res(d + int(pick.get("handicap") or 0)) == o
    if play == "总进球":
        t = hs + as_
        return (str(t) if t < 7 else "7+") == o
    if play == "比分":
        key = f"{hs}:{as_}"
        if o.endswith("其他"):
            return o[0] == _res(d) and key not in OWN[_res(d)]
        return key == o
    if play == "半全场":
        if hh is None or ha is None:
            return None
        return _res(hh - ha) + _res(d) == o
    return None


OWN = {"胜": {"1:0", "2:0", "2:1", "3:0", "3:1", "3:2", "4:0", "4:1", "4:2", "5:0", "5:1", "5:2"},
       "平": {"0:0", "1:1", "2:2", "3:3"},
       "负": {"0:1", "0:2", "1:2", "0:3", "1:3", "2:3", "0:4", "1:4", "2:4", "0:5", "1:5", "2:5"}}


def payout(plan, hits):
    """hits: {match: [odds of the picks that won]} for every match. -> (stake, return)."""
    by = {}
    for p in plan["picks"]:
        by.setdefault(p["match"], []).append(p)
    ms = list(by)
    k, mult = int(plan.get("k") or len(ms)), int(plan.get("mult") or 1)
    stake = ret = 0.0
    for sub in itertools.combinations(ms, k):
        n = 1
        for m in sub:
            n *= len(by[m])
        stake += 2 * mult * n
        r = 2 * mult
        for m in sub:
            r *= sum(hits.get(m, []))
        ret += r
    return round(stake, 2), round(ret, 2)


def run(result_of, data_dir, site_dir, gh=None, now=None, log=print):
    """result_of(pick) -> {"hs","as","hh"?,"ha"?} or None."""
    gh = gh or GH()
    now = now or time.time()
    path = os.path.join(data_dir, "jcplans.json")
    st = json.load(open(path)) if os.path.exists(path) else {}
    if gh.ok:
        try:
            for i in gh.issues(prefix="[竞彩方案]", label="jc-plan"):
                key = str(i["number"])
                if key in st:
                    continue
                m = JSON_RE.search(i.get("body") or "")
                try:
                    plan = json.loads(m.group(1)) if m else None
                except ValueError:
                    plan = None
                st[key] = {"num": i["number"], "url": i.get("html_url"), "title": i.get("title"),
                           "at": i.get("created_at"), "plan": plan, "status": "open" if plan else "bad"}
        except requests.RequestException as e:
            log(f"[jcplan] GitHub API failed: {type(e).__name__}")
    done = 0
    for key, e in st.items():
        if e["status"] != "open":
            continue
        picks = e["plan"]["picks"]
        last = max(p.get("start") or 0 for p in picks)
        if now < last + 3 * 3600:
            continue
        hits, missing = {}, False
        for p in picks:
            r = result_of(p)
            h = hit(p, r["hs"], r["as"], r.get("hh"), r.get("ha")) if r else None
            if h is None:
                missing = True
                break
            p["won"] = h
            p["score"] = f"{r['hs']}-{r['as']}" + (f"（半场 {r['hh']}-{r['ha']}）" if r.get("hh") is not None else "")
            hits.setdefault(p["match"], [])
            if h:
                hits[p["match"]].append(p["odds"])
        if missing:
            if now > last + 10 * 86400:
                e["status"] = "void"
            continue
        stake, ret = payout(e["plan"], hits)
        e.update(status="settled", stake=stake, ret=ret, net=round(ret - stake, 2), settled_at=int(now))
        done += 1
        if gh.ok:
            lines = [f"- {p['home']} vs {p['away']}：{p['play']} {p['outcome']} @{p['odds']} → {p['score']} "
                     f"{'✅' if p['won'] else '❌'}" for p in picks]
            try:
                gh.s.post(f"https://api.github.com/repos/{gh.repo}/issues/{e['num']}/comments", timeout=30, json={
                    "body": "赛后结算（按冻结时的赔率）：\n\n" + "\n".join(lines) +
                            f"\n\n本金 {stake} 元，返奖 {ret} 元，净{'赚' if ret >= stake else '亏'} {abs(ret - stake):.2f} 元。"})
                gh.s.patch(f"https://api.github.com/repos/{gh.repo}/issues/{e['num']}", json={"state": "closed"},
                           timeout=30)
            except requests.RequestException:
                pass
    json.dump(st, open(path, "w"), ensure_ascii=False)
    s = [e for e in st.values() if e["status"] == "settled"]
    out = {"generated": int(now), "plans": sorted(st.values(), key=lambda e: -(e.get("num") or 0))[:200],
           "rec": {"n": len(s), "stake": round(sum(e["stake"] for e in s), 2), "ret": round(sum(e["ret"] for e in s), 2),
                   "won": sum(e["net"] > 0 for e in s), "any": sum(e["ret"] > 0 for e in s),
                   "exp": round(sum(e["plan"].get("ev") or 0 for e in s), 2)}}
    json.dump(out, open(os.path.join(site_dir, "jcplans.json"), "w"), ensure_ascii=False)
    log(f"[jcplan] {len(st)} plans, {done} settled now, {sum(e['status'] == 'open' for e in st.values())} open")
    return out
