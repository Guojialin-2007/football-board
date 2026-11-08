"""Debug: print the names of every soccer market the board treats as 1X2 / O/U / AH (one /v4/markets call)."""
import json
import os

from . import odds as O
from .papi import Papi

api = Papi()
meta = json.load(open(os.path.join("data", "meta.json")))
sid = meta["sid"]
raw = {str(x.get("marketId")): x for x in api.get("/v4/markets") if x.get("sportId") == sid}
for mid, v in sorted(O.classify(list(raw.values()), sid).items(), key=lambda x: (x[1]["kind"], int(x[0]))):
    x = raw[mid]
    print(f"{v['kind']:4} {mid:>6} line={v['line']} | {x.get('marketName')} | {x.get('marketType')} | {x.get('period')} "
          f"| {[o.get('outcomeName') for o in x.get('outcomes') or []]}")
