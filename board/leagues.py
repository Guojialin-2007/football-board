"""The leagues shown on the board (the user's list, in this order) and how to find them among OddsPapi tournaments.

Each rule: Chinese name, country words (any must appear in the tournament's country/category), tournament-name
regex. Women's, youth and reserve competitions are never matched. Every run logs which OddsPapi tournaments
matched each league, so a wrong or missing match is visible in the Actions log.
"""

from __future__ import annotations

import re
import unicodedata

RULES = [
    ("英超", ["england"], r"^premier league$"),
    ("澳超", ["australia"], r"^a[- ]?league( men)?$"),
    ("欧冠", ["international", "europe", "uefa", "world"], r"champions league"),
    ("西甲", ["spain"], r"^(la ?liga|primera division)$"),
    ("意甲", ["italy"], r"^serie a$"),
    ("德甲", ["germany"], r"^bundesliga$"),
    ("法甲", ["france"], r"^ligue 1$"),
    ("美职联", ["usa", "united states"], r"^(mls|major league soccer)$"),
    ("墨西哥 Liga MX", ["mexico"], r"^liga mx(,? (apertura|clausura))?$"),
    ("欧联杯", ["international", "europe", "uefa", "world"], r"europa league"),
    ("欧协联", ["international", "europe", "uefa", "world"], r"conference league"),
    ("巴甲", ["brazil"], r"^(brasileiro )?serie a( betano)?$|^brasileirao"),
    ("英冠", ["england"], r"^championship$"),
    ("沙特联", ["saudi"], r"(saudi )?pro league|^roshn|^spl$"),
    ("葡超", ["portugal"], r"^(liga portugal|primeira liga)( betclic)?$"),
    ("荷甲", ["netherlands"], r"^eredivisie$"),
    ("土超", ["turkey", "turkiye"], r"^(super lig|superlig)$"),
    ("阿甲", ["argentina"], r"^(liga profesional( de futbol)?|primera division)"),
    ("日职 J1", ["japan"], r"^j\.? ?(1 )?league( 1)?$|^j1"),
    ("日职 J2", ["japan"], r"^j\.? ?(2 )?league 2$|^j\.? ?2 league$|^j2"),
    ("韩 K1", ["korea"], r"^k ?league 1$|^k1"),
    ("韩 K2", ["korea"], r"^k ?league 2$|^k2"),
    ("德乙", ["germany"], r"^2\.? bundesliga$"),
    ("法乙", ["france"], r"^ligue 2$"),
    ("西乙", ["spain"], r"^(la ?liga 2|laliga hypermotion|segunda division)$"),
]
ORDER = {name: i for i, (name, _, _) in enumerate(RULES)}
EXCLUDE = re.compile(r"women|femin|female|\bw\b|u\d\d|youth|reserve|\bii\b|\bb\b|amateur|cup|copa|play-?off|qualif",
                     re.I)
CUP_OK = {"欧冠", "欧联杯", "欧协联"}      # competitions whose name has no "cup" problem but may include qualifying


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.,' -]", " ", s)).strip()


def match(tours, log=print):
    """tours: {tournamentId: {"name", "country"}} -> {tournamentId: Chinese league name}"""
    out, found = {}, {}
    for tid, t in tours.items():
        nm, ct = norm(t.get("name")), norm(t.get("country"))
        if not nm:
            continue
        for cn, countries, rx in RULES:
            if EXCLUDE.search(nm) and not (cn in CUP_OK and not re.search(r"women|u\d\d|youth", nm)):
                continue
            if cn in CUP_OK and re.search(r"qualif", nm):
                continue
            if any(c in ct for c in countries) and re.search(rx, nm):
                out[str(tid)] = cn
                found.setdefault(cn, []).append(f"{t.get('name')} ({t.get('country')}, id {tid})")
                break
    for cn, _, _ in RULES:
        log(f"[leagues] {cn}: " + ("; ".join(found[cn][:4]) if cn in found else "NOT FOUND among OddsPapi tournaments"))
    return out
