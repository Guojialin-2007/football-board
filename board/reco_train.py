"""Weights for the default recommendation: multinomial logistic regression on signals computable live from
football-data rows + current prices. Train < 2022-07, validate after."""
import os, sys, json, math
from collections import deque, defaultdict
from datetime import datetime, timezone, timedelta
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from board import fd as F
from board.signals import power_devig
fd = F.FD(os.environ.get('DATA', 'data'))
SPL = (datetime.now(timezone.utc) - timedelta(days=3 * 365)).timestamp()   # last 3 years held out for checking
NAMES = ["市场：主客强弱", "市场：平局", "近期状态（场均积分差）", "主队近 5 场超出市场预期", "客队近 5 场超出市场预期",
         "主队状态好却开得低", "客队状态好却开得低", "主队同赔同主场一直不赢", "客队同赔同客场一直不赢", "历史交手平局比例"]
def features(p, st):
    """p: market (power de-vig) [h,d,a]; st: dict of the pre-match state."""
    return [math.log(p[0] / p[2]), math.log(p[1] / (1 - p[1])),
            st["ppg_h"] - st["ppg_a"], st["over_h"] / 5, st["over_a"] / 5,
            float(st["ppg_h"] >= 2.0 and st["ppg_a"] <= 1.2 and p[0] <= 0.5),
            float(st["ppg_a"] >= 2.0 and st["ppg_h"] <= 1.2 and p[2] <= 0.5),
            float(st["nowin_h"]), float(st["nowin_a"]), st["h2h_draw"]]
X, Y, P, C, D = [], [], [], [], []
for lg, rs in fd.rows.items():
    form = defaultdict(lambda: deque(maxlen=5)); same = defaultdict(list); h2h = defaultdict(list)
    for r in rs:
        c = r.get("c")
        res = 0 if r["hg"] > r["ag"] else 1 if r["hg"] == r["ag"] else 2
        if not c or min(c) <= 1:
            continue
        p = power_devig(c)
        fh, fa = form[r["h"]], form[r["a"]]
        if len(fh) >= 3 and len(fa) >= 3:
            sh = [t for t in same[(r["h"], "H")] if abs(t[0] - c[0]) <= 0.15][-5:]
            sa = [t for t in same[(r["a"], "A")] if abs(t[0] - c[2]) <= 0.15][-5:]
            past = h2h[frozenset((r["h"], r["a"]))]
            st = {"ppg_h": sum(t[0] for t in fh) / len(fh), "ppg_a": sum(t[0] for t in fa) / len(fa),
                  "over_h": sum(t[1] for t in fh) * 5 / len(fh), "over_a": sum(t[1] for t in fa) * 5 / len(fa),
                  "nowin_h": len(sh) >= 3 and not any(t[1] for t in sh), "nowin_a": len(sa) >= 3 and not any(t[1] for t in sa),
                  "h2h_draw": (sum(1 for t in past if t == 1) + 0.27 * 3) / (len(past) + 3)}
            X.append(features(p, st)); Y.append(res); P.append(p); C.append(c); D.append(r["d"] < SPL)
        eh, ea = 3 * p[0] + p[1], 3 * p[2] + p[1]
        ph = 3 if res == 0 else 1 if res == 1 else 0; pa = 3 if res == 2 else 1 if res == 1 else 0
        fh.append((ph, ph - eh)); fa.append((pa, pa - ea))
        same[(r["h"], "H")].append((c[0], res == 0)); same[(r["a"], "A")].append((c[2], res == 2))
        h2h[frozenset((r["h"], r["a"]))].append(res)
X, Y, P, C, D = map(np.array, (X, Y, P, C, D))
print("matches", len(Y), "train", D.sum(), "test", (~D).sum())
m = LogisticRegression(max_iter=3000, C=1.0).fit(X[D], Y[D])
Pm = m.predict_proba(X[~D])
base, ll = log_loss(Y[~D], P[~D], labels=[0, 1, 2]), log_loss(Y[~D], Pm, labels=[0, 1, 2])
print(f"log-loss market {base:.5f}  model {ll:.5f}  ({(ll-base)*1000:+.2f}‰)")
def bet_stats(Pr, th):
    v = []
    for pp, cc, yy in zip(Pr, C[~D], Y[~D]):
        for i in range(3):
            if pp[i] * cc[i] - 1 >= th:
                v.append(cc[i] - 1 if yy == i else -1.0)
    n = len(v); mu = sum(v) / n; sd = (sum((t-mu)**2 for t in v)/(n-1))**.5; h = 1.96*sd/n**.5
    return {"n": n, "roi": round(mu, 4), "ci": [round(mu - h, 4), round(mu + h, 4)]}
def bets(Pr, th, need_market_ok):
    v = []
    for pp, pk, cc, yy in zip(Pr, P[~D], C[~D], Y[~D]):
        for i in range(3):
            if pp[i] * cc[i] - 1 >= th and (not need_market_ok or pk[i] * cc[i] - 1 >= -0.02):
                v.append(cc[i] - 1 if yy == i else -1.0)
    n = len(v); mu = sum(v) / n; sd = (sum((t-mu)**2 for t in v)/(n-1))**.5; h = 1.96*sd/n**.5
    return f"{n:5d} 注 {mu*100:+5.1f}% [{(mu-h)*100:+5.1f},{(mu+h)*100:+5.1f}]"
for th in (0.02, 0.04, 0.06):
    print(f"推荐（模型期望 ≥{th*100:.0f}%，收盘价）: {bets(Pm, th, False)}")
# the "least bad" default when nothing qualifies: always taking the top model-EV outcome
v = []
for pp, cc, yy in zip(Pm, C[~D], Y[~D]):
    i = int(np.argmax(pp * cc)); v.append(cc[i] - 1 if yy == i else -1.0)
print(f"每场都买模型期望最高的那一项: {len(v)} 注 {np.mean(v)*100:+.1f}%")
coef = m.coef_ - m.coef_.mean(axis=0)
print("权重（对主胜/平/客胜的影响，已中心化）:")
for j, n in enumerate(NAMES):
    print(f"  {n:22s} 主 {coef[0][j]:+.3f}  平 {coef[1][j]:+.3f}  客 {coef[2][j]:+.3f}")
json.dump({"names": NAMES, "coef": m.coef_.tolist(), "intercept": m.intercept_.tolist(),
           "trained_until": datetime.fromtimestamp(SPL, timezone.utc).strftime("%Y-%m-%d"), "n_train": int(D.sum()), "n_test": int((~D).sum()),
           "test_logloss_market": base, "test_logloss_model": ll, "bt4": bet_stats(Pm, 0.04),
           "test_from": datetime.fromtimestamp(SPL, timezone.utc).strftime("%Y-%m-%d")},
          open(os.path.join(os.path.dirname(__file__), 'reco_weights.json'), 'w'), ensure_ascii=False)
