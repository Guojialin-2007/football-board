"""20 years of results and odds from football-data.co.uk (free CSVs), for head-to-head and "same odds" history.

Main leagues  https://www.football-data.co.uk/mmz4281/<season>/<code>.csv   (season 2526 = 2025/26)
  Date (dd/mm/yy or dd/mm/yyyy), HomeTeam, AwayTeam, FTHG, FTAG, opening odds PSH/PSD/PSA (Pinnacle, from
  2012) or B365H/D/A, closing PSCH/PSCD/PSCA, Asian handicap AHh (opening line, home) / AHCh (closing) with
  PAHH/PAHA and PCAHH/PCAHA, over/under 2.5 P>2.5 / P<2.5 and PC>2.5 / PC<2.5.
Extra leagues https://www.football-data.co.uk/new/<CODE>.csv   (all seasons in one file)
  Date, Home, Away, HG, AG, closing odds PSCH/PSCD/PSCA (or AvgCH/AvgCD/AvgCA, B365CH/...).
Past seasons are downloaded once and kept in data/fd; the current season and the extra files are refreshed daily.
Leagues without football-data coverage (UEFA cups, Saudi, A-League, K League) fall back to ESPN (about a year).
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from datetime import datetime, timezone

import requests

from .stats import same_team, toks

BASE = "https://www.football-data.co.uk"
MAIN = {"英超": "E0", "英冠": "E1", "西甲": "SP1", "西乙": "SP2", "意甲": "I1", "德甲": "D1", "德乙": "D2",
        "法甲": "F1", "法乙": "F2", "荷甲": "N1", "葡超": "P1", "土超": "T1"}
EXTRA = {"阿甲": "ARG", "巴甲": "BRA", "日职 J1": "JPN", "墨西哥 Liga MX": "MEX", "美职联": "USA"}
# football-data's short names -> words our matcher understands
FD_ALIAS = {"ath madrid": "atletico madrid", "ath bilbao": "athletic bilbao", "nott'm forest": "nottingham forest",
            "man united": "manchester united", "man city": "manchester city", "sheffield weds": "sheffield wednesday",
            "sheffield united": "sheffield united", "qpr": "queens park rangers", "west brom": "west bromwich albion",
            "wolves": "wolverhampton wanderers", "spurs": "tottenham", "m'gladbach": "monchengladbach",
            "ein frankfurt": "eintracht frankfurt", "fc koln": "cologne koln", "rennes": "stade rennais", "nacional": "nacional madeira", "sp gijon": "sporting gijon", "paris sg": "paris saint germain",
            "sp lisbon": "sporting cp", "sp braga": "braga", "vitoria": "vitoria guimaraes", "betis": "real betis",
            "sociedad": "real sociedad", "espanol": "espanyol", "celta": "celta vigo", "la coruna": "deportivo la coruna",
            "inter": "inter milan", "milan": "ac milan", "verona": "hellas verona", "spal": "spal ferrara",
            "psv eindhoven": "psv", "nijmegen": "nec nijmegen", "for sittard": "fortuna sittard", "leverkusen": "bayer leverkusen",
            "dortmund": "borussia dortmund", "st pauli": "saint pauli", "greuther furth": "greuther furth",
            "buyuksehyr": "istanbul basaksehir", "ad. demirspor": "adana demirspor"}


def _season_codes(n=20, now=None):
    now = now or datetime.now(timezone.utc)
    start = now.year if now.month >= 7 else now.year - 1
    return [f"{(y % 100):02d}{((y + 1) % 100):02d}" for y in range(start - n + 1, start + 1)], f"{start % 100:02d}{(start + 1) % 100:02d}"


def _f(row, *names):
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            try:
                return float(v)
            except ValueError:
                pass
    return None


def _trio(row, a, b, c):
    xs = [_f(row, x) for x in (a, b, c)]
    return xs if all(x and x > 1 for x in xs) else None


def _date(s):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return None


def parse_csv(text, extra=False):
    out = []
    rdr = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for row in rdr:
        h = (row.get("HomeTeam") or row.get("Home") or "").strip()
        a = (row.get("AwayTeam") or row.get("Away") or "").strip()
        hg, ag = _f(row, "FTHG", "HG"), _f(row, "FTAG", "AG")
        d = _date(row.get("Date"))
        if not h or not a or hg is None or ag is None or not d:
            continue
        r = {"d": d, "h": h, "a": a, "hg": int(hg), "ag": int(ag)}
        o = _trio(row, "PSH", "PSD", "PSA") or _trio(row, "B365H", "B365D", "B365A")
        c = (_trio(row, "PSCH", "PSCD", "PSCA") or _trio(row, "AvgCH", "AvgCD", "AvgCA")
             or _trio(row, "B365CH", "B365CD", "B365CA"))
        if o:
            r["o"] = [round(x, 2) for x in o]
        if c:
            r["c"] = [round(x, 2) for x in c]
        if not extra:
            ah, ahc = _f(row, "AHh", "BbAHh"), _f(row, "AHCh")
            if ah is not None:
                r["ah"] = ah
                odds = [_f(row, "PAHH", "B365AHH", "BbAvAHH"), _f(row, "PAHA", "B365AHA", "BbAvAHA")]
                if all(odds):
                    r["aho"] = odds
            if ahc is not None:
                r["ahc"] = ahc
                odds = [_f(row, "PCAHH", "B365CAHH"), _f(row, "PCAHA", "B365CAHA")]
                if all(odds):
                    r["ahco"] = odds
            ou = [_f(row, "P>2.5", "B365>2.5", "BbAv>2.5"), _f(row, "P<2.5", "B365<2.5", "BbAv<2.5")]
            if all(ou):
                r["ou"] = ou
            ouc = [_f(row, "PC>2.5", "B365C>2.5"), _f(row, "PC<2.5", "B365C<2.5")]
            if all(ouc):
                r["ouc"] = ouc
        out.append(r)
    return out


def _get(s, url):
    r = s.get(url, timeout=60)
    if r.status_code != 200:
        return None
    try:
        return r.content.decode("utf-8")
    except UnicodeDecodeError:
        return r.content.decode("latin-1")


def fetch(data_dir, leagues, session=None, log=print, budget_s=600, seasons=20):
    s = session or requests.Session()
    d = os.path.join(data_dir, "fd")
    os.makedirs(d, exist_ok=True)
    codes, cur = _season_codes(seasons)
    t0, got = time.time(), 0
    for lg in leagues:
        if lg in MAIN:
            for sc in codes:
                p = os.path.join(d, f"{MAIN[lg]}_{sc}.json")
                fresh = os.path.exists(p) and (sc != cur or time.time() - os.path.getmtime(p) < 20 * 3600)
                if fresh or time.time() - t0 > budget_s:
                    continue
                txt = _get(s, f"{BASE}/mmz4281/{sc}/{MAIN[lg]}.csv")
                json.dump(parse_csv(txt) if txt else [], open(p, "w"))
                got += 1
                time.sleep(0.3)
        elif lg in EXTRA:
            p = os.path.join(d, f"{EXTRA[lg]}.json")
            if os.path.exists(p) and time.time() - os.path.getmtime(p) < 20 * 3600:
                continue
            if time.time() - t0 > budget_s:
                continue
            txt = _get(s, f"{BASE}/new/{EXTRA[lg]}.csv")
            if txt:
                json.dump(parse_csv(txt, extra=True), open(p, "w"))
                got += 1
    log(f"[fd] football-data.co.uk: {got} file(s) downloaded")


class FD:
    """All football-data rows, by league, with team-name lookup."""

    def __init__(self, data_dir, since_years=20):
        d = os.path.join(data_dir, "fd")
        self.rows = {}
        cut = time.time() - since_years * 365.25 * 86400
        inv = {v: k for k, v in MAIN.items()}
        inv.update({v: k for k, v in EXTRA.items()})
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                code = f.split("_")[0].replace(".json", "")
                lg = inv.get(code)
                if not lg:
                    continue
                for r in json.load(open(os.path.join(d, f))):
                    if r["d"] >= cut:
                        self.rows.setdefault(lg, []).append(r)
        for v in self.rows.values():
            v.sort(key=lambda r: r["d"])
        self.teams = {lg: sorted({r["h"] for r in v} | {r["a"] for r in v}) for lg, v in self.rows.items()}
        self.cache = {}

    def n(self):
        return sum(len(v) for v in self.rows.values())

    def team(self, name, league=None):
        """football-data name for a team, searched in its league first, then everywhere."""
        key = (name, league)
        if key in self.cache:
            return self.cache[key]
        pools = ([self.teams.get(league, [])] if league in self.teams else []) + [sorted({t for v in self.teams.values() for t in v})]
        hit = None
        for pool in pools:
            cands = [t for t in pool if same_team(name, FD_ALIAS.get(t.lower(), t)) or same_team(name, t)]
            if cands:
                hit = min(cands, key=lambda t: abs(len(toks(FD_ALIAS.get(t.lower(), t))) - len(toks(name))))
                break
        self.cache[key] = hit
        return hit

    def all_rows(self):
        for v in self.rows.values():
            yield from v
