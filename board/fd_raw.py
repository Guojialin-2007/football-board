"""Research only: download football-data CSVs with every bookmaker column we care about (opening + closing,
bet365 / Pinnacle / market max / market average) and match stats, and save them compactly.
Run in GitHub Actions (the board's container cannot reach football-data.co.uk)."""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import sys
import time

import requests

from .fd import BASE, MAIN, _season_codes

KEEP = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HTHG", "HTAG", "HS", "AS", "HST", "AST", "HC", "AC",
        "B365H", "B365D", "B365A", "PSH", "PSD", "PSA", "MaxH", "MaxD", "MaxA", "AvgH", "AvgD", "AvgA",
        "B365CH", "B365CD", "B365CA", "PSCH", "PSCD", "PSCA", "MaxCH", "MaxCD", "MaxCA", "AvgCH", "AvgCD", "AvgCA",
        "B365>2.5", "B365<2.5", "P>2.5", "P<2.5", "Max>2.5", "Max<2.5", "Avg>2.5", "Avg<2.5",
        "B365C>2.5", "B365C<2.5", "PC>2.5", "PC<2.5", "MaxC>2.5", "MaxC<2.5", "AvgC>2.5", "AvgC<2.5",
        "AHh", "B365AHH", "B365AHA", "PAHH", "PAHA", "MaxAHH", "MaxAHA", "AvgAHH", "AvgAHA",
        "AHCh", "B365CAHH", "B365CAHA", "PCAHH", "PCAHA", "MaxCAHH", "MaxCAHA", "AvgCAHH", "AvgCAHA"]


def main(out_dir="raw", seasons=8):
    os.makedirs(out_dir, exist_ok=True)
    s = requests.Session()
    codes, _ = _season_codes(seasons)
    for lg, code in MAIN.items():
        rows = []
        for sc in codes:
            try:
                r = s.get(f"{BASE}/mmz4281/{sc}/{code}.csv", timeout=30)
            except requests.RequestException:
                continue
            if r.status_code != 200:
                continue
            for row in csv.DictReader(io.StringIO(r.content.decode("latin-1").lstrip("﻿"))):
                rows.append([sc] + [row.get(k, "") for k in KEEP])
            time.sleep(0.3)
        with gzip.open(os.path.join(out_dir, f"{code}.json.gz"), "wt", encoding="utf-8") as f:
            json.dump({"league": lg, "cols": ["season"] + KEEP, "rows": rows}, f)
        print(lg, code, len(rows), file=sys.stderr)


if __name__ == "__main__":
    main()
