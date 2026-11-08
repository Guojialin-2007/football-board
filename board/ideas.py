"""Ideas ("思路") submitted as GitHub issues (form .github/ISSUE_TEMPLATE/idea.yml), settled after the match.

Each run:
  1. read issues labelled 'idea' (or titled "[思路] ...") through the GitHub API with the workflow's token
  2. parse the form; remember the line / odds at submission time from the board (Pinnacle) if not given
  3. once the match is over and the score is known: Asian settlement -> win / half / push / half-loss / loss,
     reply on the issue with the result and close it (good and bad ideas both stay for review)
  4. track record overall, per market and per reason tag -> site/ideas.json ("which kind of reasoning works")
Ideas submitted after kickoff are kept but not counted.
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

from . import history as H

API = "https://api.github.com"
FIELDS = {"比赛ID": "match", "比赛": "game", "玩法": "market", "选择": "pick", "盘口": "line", "赔率": "odds",
          "理由（可多选）": "tags", "思路": "why"}


def parse(body):
    out, cur, buf = {}, None, []

    def flush():
        if cur:
            txt = "\n".join(buf).strip()
            out[cur] = "" if txt in ("_No response_", "None") else txt

    for line in (body or "").splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            flush()
            cur, buf = FIELDS.get(m.group(1).strip()), []
            continue
        buf.append(line)
    flush()
    out["tags"] = re.findall(r"- \[[xX]\]\s*(.+)", out.get("tags", ""))
    for k in ("line", "odds"):
        try:
            out[k] = float(str(out.get(k, "")).replace("+", "").strip()) if out.get(k) else None
        except ValueError:
            out[k] = None
    return out


class GH:
    def __init__(self, repo=None, token=None, session=None):
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
        self.s = session or requests.Session()
        tok = token or os.environ.get("GITHUB_TOKEN", "")
        if tok:
            self.s.headers["Authorization"] = f"Bearer {tok}"
        self.s.headers["Accept"] = "application/vnd.github+json"
        self.ok = bool(self.repo and tok)

    def issues(self):
        out = []
        for page in range(1, 20):
            r = self.s.get(f"{API}/repos/{self.repo}/issues", params={"state": "all", "per_page": 100, "page": page},
                           timeout=30)
            r.raise_for_status()
            rows = [i for i in r.json() if "pull_request" not in i]
            out += rows
            if len(r.json()) < 100:
                break
        return [i for i in out if any(l.get("name") == "idea" for l in i.get("labels") or [])
                or str(i.get("title", "")).startswith("[思路]")]

    def reply_and_close(self, num, text, label):
        self.s.post(f"{API}/repos/{self.repo}/issues/{num}/comments", json={"body": text}, timeout=30)
        self.s.post(f"{API}/repos/{self.repo}/issues/{num}/labels", json={"labels": [label]}, timeout=30)
        self.s.patch(f"{API}/repos/{self.repo}/issues/{num}", json={"state": "closed"}, timeout=30)

    def ensure_label(self):
        self.s.post(f"{API}/repos/{self.repo}/labels", json={"name": "idea", "color": "f5b942"}, timeout=30)


def _iso(t):
    from .papi import ts
    return ts(t)


def snapshot(card, market, pick):
    """Line and Pinnacle odds of the chosen side as shown on the board now."""
    ref = (card.get("books") or {}).get("pinnacle") or next(iter((card.get("books") or {}).values()), None)
    if not ref:
        return None, None
    if market == "大小球" and ref.get("ou"):
        n = ref["ou"]["now"]
        return n["line"], n["odds"][0 if pick == "大" else 1]
    if market == "亚盘" and ref.get("ah"):
        n = ref["ah"]["now"]
        return n["line"], n["odds"][0 if pick == "主队" else 1]
    if market == "胜平负" and ref.get("1x2"):
        n = ref["1x2"]["now"]
        return None, n["odds"][{"主胜": 0, "平局": 1, "客胜": 2}.get(pick, 0)]
    return None, None


def settle(idea, hs, as_):
    m, p, L, o = idea["market"], idea["pick"], idea.get("line"), idea.get("odds") or 1.9
    if m == "大小球" and L is not None:
        r = (H.settle_over if p == "大" else H.settle_under)(hs + as_, L, o)
    elif m == "亚盘" and L is not None:
        r = (H.settle_home if p == "主队" else H.settle_away)(hs - as_, L, o)
    elif m == "胜平负":
        won = {"主胜": hs > as_, "平局": hs == as_, "客胜": hs < as_}.get(p)
        r = (o - 1) if won else -1.0
    else:
        return None
    word = ("赢" if r >= (o - 1) - 1e-9 else "赢半") if r > 0 else ("走水" if r == 0 else ("输半" if r > -1 + 1e-9 else "输"))
    return {"ret": round(r, 4), "word": word}


def run(cards, result_of, data_dir, site_dir, gh=None, now=None, log=print):
    """cards: {fixtureId: card} on the board now. result_of(idea) -> (hs, as) or None."""
    gh = gh or GH()
    now = now or time.time()
    path = os.path.join(data_dir, "ideas.json")
    st = json.load(open(path)) if os.path.exists(path) else {}
    if not gh.ok:
        log("[ideas] no GitHub token/repo in the environment: skipped")
    else:
        try:
            gh.ensure_label()
            issues = gh.issues()
        except requests.RequestException as e:
            log(f"[ideas] GitHub API failed: {type(e).__name__}")
            issues = []
        for i in issues:
            key = str(i["number"])
            if key in st:
                continue
            f = parse(i.get("body"))
            if not f.get("match") or f.get("market") not in ("大小球", "亚盘", "胜平负"):
                continue
            card = cards.get(f["match"]) or {}
            line, odds = snapshot(card, f["market"], f.get("pick")) if card else (None, None)
            f["line"] = f["line"] if f.get("line") is not None else line
            f["odds"] = f["odds"] or odds
            f.update(num=i["number"], url=i.get("html_url"), user=(i.get("user") or {}).get("login"),
                     at=_iso(i.get("created_at")), status="open",
                     start=card.get("start"), home=card.get("home"), away=card.get("away"), league=card.get("league"))
            if f["start"] and f["at"] and f["at"] > f["start"]:
                f["status"] = "late"                     # submitted after kickoff: shown, never counted
            st[key] = f
        for key, f in st.items():
            if f["status"] not in ("open", "late") or not f.get("start") or now < f["start"] + 3 * 3600:
                continue
            res = result_of(f)
            if not res:
                continue
            s = settle(f, *res)
            if not s:
                continue
            f.update(score=f"{res[0]}-{res[1]}", ret=s["ret"], word=s["word"],
                     status="settled" if f["status"] == "open" else "late-settled")
            ok = s["ret"] > 0
            text = (f"{'✅' if ok else ('➖' if s['ret'] == 0 else '❌')} **{s['word']}**　比分 {res[0]}-{res[1]}　"
                    f"（{f['market']} {f['pick']} {'' if f.get('line') is None else f['line']} @ {f.get('odds') or '—'}，"
                    f"每 1 元 {s['ret']:+.2f}）" + ("\n\n（开赛后提交，不计入战绩）" if f["status"] == "late-settled" else ""))
            try:
                gh.reply_and_close(f["num"], text, "✅正确" if ok else ("➖走水" if s["ret"] == 0 else "❌错误"))
            except requests.RequestException:
                pass
    json.dump(st, open(path, "w"), ensure_ascii=False)
    done = [f for f in st.values() if f["status"] == "settled"]

    def rec(xs):
        n = len(xs)
        return {"n": n, "win": sum(x["ret"] > 0 for x in xs), "push": sum(x["ret"] == 0 for x in xs),
                "roi": round(sum(x["ret"] for x in xs) / n, 4) if n else None}
    tags = {}
    for x in done:
        for t in x.get("tags") or ["（未选理由）"]:
            tags.setdefault(t, []).append(x)
    out = {"all": rec(done), "market": {m: rec([x for x in done if x["market"] == m]) for m in ("大小球", "亚盘", "胜平负")},
           "tags": {t: rec(xs) for t, xs in sorted(tags.items(), key=lambda kv: -len(kv[1]))},
           "ideas": sorted(st.values(), key=lambda f: -(f.get("at") or 0))[:300]}
    json.dump(out, open(os.path.join(site_dir, "ideas.json"), "w"), ensure_ascii=False)
    log(f"[ideas] {len(st)} ideas, {len(done)} settled")
    return out
