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

import requests

HOST = "https://api.oddspapi.io"
COOLDOWN = {"/v4/historical-odds": 5.2, "/v4/fixtures": 2.1, "/v4/markets": 1.1}


class Papi:
    def __init__(self, key=None, session=None, sleep=time.sleep):
        self.key = (key or os.environ.get("ODDSPAPI_KEY", "")).strip()
        if not self.key:
            raise RuntimeError("ODDSPAPI_KEY is not set (GitHub: Settings > Secrets > Actions > ODDSPAPI_KEY)")
        self.s = session or requests.Session()
        self.last = {}
        self.sleep = sleep
        self.calls = {}

    def get(self, path, **params):
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
