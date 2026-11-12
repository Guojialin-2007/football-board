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
    ("欧冠", ["international", "europe", "uefa", "world"], r"^uefa champions league$"),
    ("西甲", ["spain"], r"^(la ?liga|primera division)$"),
    ("意甲", ["italy"], r"^serie a$"),
    ("德甲", ["germany"], r"^bundesliga$"),
    ("法甲", ["france"], r"^ligue 1$"),
    ("美职联", ["usa", "united states"], r"^(mls|major league soccer)$"),
    ("墨西哥 Liga MX", ["mexico"], r"^liga mx(,? (apertura|clausura))?$"),
    ("欧联杯", ["international", "europe", "uefa", "world"], r"^uefa europa league$"),
    ("欧协联", ["international", "europe", "uefa", "world"], r"^uefa (europa )?conference league$"),
    ("巴甲", ["brazil"], r"^(brasileiro )?serie a( betano)?$|^brasileirao"),
    ("英冠", ["england"], r"^championship$"),
    ("沙特联", ["saudi"], r"(saudi )?pro league|^roshn|^spl$"),
    ("葡超", ["portugal"], r"^(liga portugal|primeira liga)( betclic)?$"),
    ("荷甲", ["netherlands"], r"^eredivisie$"),
    ("土超", ["turkey", "turkiye"], r"^(super lig|superlig)$"),
    ("阿甲", ["argentina"], r"^(liga profesional( de futbol)?|primera division|primera lpf)$"),
    ("日职 J1", ["japan"], r"^j\.? ?(1 )?league( 1)?$|^j1"),
    ("日职 J2", ["japan"], r"^j\.? ?(2 )?league 2$|^j\.? ?2 league$|^j2"),
    ("韩 K1", ["korea"], r"^k[- ]?league 1$|^k1"),
    ("韩 K2", ["korea"], r"^k[- ]?league 2$|^k2"),
    ("德乙", ["germany"], r"^2\.? bundesliga$"),
    ("法乙", ["france"], r"^ligue 2$"),
    ("西乙", ["spain"], r"^(la ?liga 2|laliga hypermotion|segunda division)$"),
    ("欧国联", ["international"], r"^uefa nations league$"),   # split into A/B/C/D below
]
ORDER = {name: i for i, (name, _, _) in enumerate(RULES)}

# 2026-27 UEFA Nations League tiers (draw of 12 Feb 2026; the C/D "or" slots settled by the fixtures:
# Latvia and Luxembourg play in C, Gibraltar and Malta in D). OddsPapi has one tournament for all four.
NL_TIERS = {
    "A": ["Portugal", "Spain", "France", "Germany", "Italy", "Netherlands", "Denmark", "Croatia", "Serbia", "Belgium",
          "England", "Norway", "Wales", "Czechia", "Czech Republic", "Greece", "Turkey", "Turkiye"],
    "B": ["Scotland", "Hungary", "Poland", "Israel", "Switzerland", "Bosnia and Herzegovina", "Bosnia & Herzegovina",
          "Bosnia-Herzegovina", "Austria", "Ukraine", "Slovenia", "Georgia", "Republic of Ireland", "Ireland", "Romania",
          "Sweden", "North Macedonia", "Northern Ireland", "Kosovo"],
    "C": ["Iceland", "Albania", "Montenegro", "Kazakhstan", "Finland", "Slovakia", "Bulgaria", "Armenia", "Belarus",
          "Faroe Islands", "Cyprus", "Estonia", "Latvia", "Luxembourg", "Moldova", "San Marino"],
    "D": ["Azerbaijan", "Lithuania", "Gibraltar", "Malta", "Liechtenstein", "Andorra"],
}
NL_NAMES = ["欧国联A", "欧国联B", "欧国联C", "欧国联D"]
_NL = {}


def nl_tier(home, away):
    """'欧国联A'..'欧国联D' from the two teams (both should agree; one known team is enough), else '欧国联'."""
    if not _NL:
        for t, names in NL_TIERS.items():
            for n in names:
                _NL[norm(n).replace("&", "and")] = t
    got = [_NL.get(norm(x).replace("&", "and")) for x in (home, away)]
    got = [g for g in got if g]
    return f"欧国联{got[0]}" if got and all(g == got[0] for g in got) else "欧国联"


def shown_names():
    """League names as shown on the board, in order (欧国联 expanded into its four tiers)."""
    out = []
    for n, _, _ in RULES:
        out += NL_NAMES if n == "欧国联" else [n]
    return out
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
