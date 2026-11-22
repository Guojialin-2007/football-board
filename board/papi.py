"""Minimal OddsPapi client (api.oddspapi.io). Key from the ODDSPAPI_KEY environment variable, never printed.

Free plan (as used since 2026-10): /v4/historical-odds is free with a ~5 s cooldown and returns every
timestamped price change of up to 3 bookmakers for one fixture; /v4/fixtures, /v4/markets, /v4/sports,
/v4/tournaments count against a monthly allowance, so the board caches them.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import json
import re
import requests

HOST = "https://api.oddspapi.io"
COOLDOWN = {"/v4/historical-odds": 5.2, "/v4/fixtures": 2.1, "/v4/markets": 1.1}


# endpoints that use the monthly allowance (https://oddspapi.io/en/docs/requests-and-quota); the free plan's quota
# resets at 00:00 UTC on the 1st of each calendar month
COUNTED = {"/v4/players", "/v4/settlements", "/v4/fixtures", "/v4/fixture", "/v4/odds-by-tournaments", "/v4/languages",
           "/v4/sports", "/v4/bookmakers", "/v4/markets", "/v4/tournaments", "/v4/participants", "/v4/scores", "/v4/odds"}


class QuotaExceeded(RuntimeError):
    pass


class Papi:
    def __init__(self, key=None, session=None, sleep=time.sleep):
        self.key = (key or os.environ.get("ODDSPAPI_KEY", "")).strip()
        if not self.key:
            raise RuntimeError("ODDSPAPI_KEY is not set (GitHub: Settings > Secrets > Actions > ODDSPAPI_KEY)")
        self.s = session or requests.Session()
        self.last = {}
        self.sleep = sleep
        self.calls = {}

    # ---- monthly allowance -------------------------------------------------------------------------------------
    ledger_path = None                     # set by the caller (data/quota.json): counted calls this month, committed

    def _month(self):
        return datetime.utcnow().strftime("%Y-%m")

    def ledger(self):
        led = {}
        if self.ledger_path and os.path.exists(self.ledger_path):
            try:
                led = json.load(open(self.ledger_path))
            except (OSError, ValueError):
                led = {}
        if led.get("month") != self._month():
            led = {"month": self._month(), "used": 0, "limit": led.get("limit") or 250, "out": False}
        return led

    def _save(self, led):
        if self.ledger_path:
            os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
            json.dump(led, open(self.ledger_path, "w"))

    def sync_account(self):
        """/v4/account is free: read the plan's limit and what is used (field names guessed defensively)."""
        led = self.ledger()
        try:
            js = self.get("/v4/account")
        except Exception as e:             # noqa: BLE001  never stop the board over the account call
            print(f"[papi] account not read: {str(e)[:120]}", file=sys.stderr)
            return led
        flat = {}

        def walk(x, pre=""):
            if isinstance(x, dict):
                for k, v in x.items():
                    walk(v, f"{pre}{k}.".lower())
            elif isinstance(x, (int, float)) and not isinstance(x, bool):
                flat[pre.rstrip(".")] = x
        walk(js)
        lim = next((v for k, v in flat.items() if "limit" in k and "request" in k), None)
        used = next((v for k, v in flat.items() if "request" in k and ("used" in k or "count" in k or "made" in k)), None)
        left = next((v for k, v in flat.items() if "request" in k and ("remain" in k or "left" in k)), None)
        print(f"[papi] account fields: {sorted(flat)}", file=sys.stderr)
        if lim:
            led["limit"] = int(lim)
        if used is not None:
            led["used"] = max(led["used"], int(used))
        elif left is not None and led.get("limit"):
            led["used"] = max(led["used"], int(led["limit"]) - int(left))
        self._save(led)
        return led

    def allow(self, n=1, reserve=0.12):
        """May we spend n counted calls now? Keeps to an even pace through the month, holding back `reserve`
        of the allowance for the last days, and never after the plan has said the limit is reached."""
        led = self.ledger()
        if led.get("out"):
            return False
        now = datetime.utcnow()
        import calendar
        days = calendar.monthrange(now.year, now.month)[1]
        frac = min(1.0, (now.day - 1 + now.hour / 24 + 1 / 24) / days + 1 / days)    # one day ahead of the even pace
        cap = int(led["limit"] * (1 - reserve) * frac) + 3
        return led["used"] + n <= min(cap, int(led["limit"] * (1 - reserve / 3)))

    def probe_history(self, fixture_id):
        """While the allowance is out, try one free odds call per run to see whether odds are served again."""
        led = self.ledger()
        if not led.get("hist_out") or not fixture_id:
            return
        self.probe = True
        try:
            self.get("/v4/historical-odds", fixtureId=fixture_id, bookmakers="pinnacle")
        except RuntimeError:
            pass
        finally:
            self.probe = False

    def quota_note(self):
        led = self.ledger()
        return {"month": led["month"], "used": led["used"], "limit": led["limit"], "out": bool(led.get("out")),
                "odds_out": bool(led.get("hist_out"))}

    def get(self, path, **params):
        counted = path in COUNTED
        led0 = self.ledger()
        if (counted and led0.get("out")) or (path == "/v4/historical-odds" and led0.get("hist_out")
                                             and not getattr(self, "probe", False)):
            raise QuotaExceeded(f"OddsPapi monthly allowance used up ({path} not called)")
        wait = COOLDOWN.get(path, 1.1) - (time.time() - self.last.get(path, 0))
        if wait > 0:
            self.sleep(wait)
        params["apiKey"] = self.key
        r = None
        for attempt in range(6):
            try:
                r = self.s.get(HOST + path, params=params, timeout=60)
            except requests.RequestException as e:
                self.last[path] = time.time()
                if attempt == 5:
                    raise RuntimeError(f"network error on {path}: {type(e).__name__}") from None
                print(f"[papi] network error ({type(e).__name__}), retry in {10 * (attempt + 1)} s", file=sys.stderr)
                self.sleep(10 * (attempt + 1))
                continue
            self.last[path] = time.time()
            if r.status_code == 429 and "LIMIT" not in r.text.upper():
                self.sleep(6 * (attempt + 1))
                continue
            break
        self.calls[path] = self.calls.get(path, 0) + 1
        limit_hit = r.status_code == 429 and "LIMIT" in r.text.upper()
        if counted or limit_hit or path == "/v4/historical-odds":
            led = self.ledger()
            led["used"] += 1 if counted else 0
            if limit_hit:
                led["out"] = True
                if path == "/v4/historical-odds":
                    led["hist_out"] = True
                m = re.search(r"limit of (\d+)", r.text)
                if m:
                    led["limit"] = int(m.group(1))
                    led["used"] = max(led["used"], led["limit"])
            if path == "/v4/historical-odds" and r.status_code == 200 and led.get("hist_out"):
                led["hist_out"] = False
            self._save(led)
            if limit_hit:
                raise QuotaExceeded(f"OddsPapi {path}: monthly allowance used up ({led['limit']} requests)")
        if r.status_code != 200:
            raise RuntimeError(f"OddsPapi {path} {r.status_code}: {r.text[:300].replace(self.key, '***')}")
        return r.json()


def ts(x):
    try:
        return int(datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def name(x):
    return str(x.get("sportName") or x.get("tournamentName") or x.get("name") or "")
