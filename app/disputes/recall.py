"""Recall check (spec §9): re-ask every conditional forecast with the parties' names replaced by their roles.

A forecast that moves when the names go away may be drawing on what Jev remembers about the parties rather than on
the record in front of it. Every place Jev sees the parties is rewritten: the case block, the question's context and
assumptions, the path facts, the record quotes and the evidence context. Aliases come from the instance's parties
(borrower, counterparty, the notes' issuer, the case reference) plus the names, numbers and places in the record that
identify a party, grouped by party below: entities, people, patents, case numbers and addresses, each as a pattern. The check is stored beside the analysis and
never changes its probabilities.
"""

from __future__ import annotations

import asyncio
import re

from app.disputes.forecast import Forecaster, ForecastJudge, Judgment, answer_distribution
from app.domain.investigation import DisputeInstance

DEFENDANT = "the defendant (the issuer of the notes)"
PLAINTIFF = "the plaintiff"
MOVED = 0.10  # a question moving more than this (10 points) is listed in the summary

# --- What identifies a party in the Akoustis record, grouped by party -------------------------------------------
# Extracted from the pre-D snapshot var/evidence/akoustis_20240620.sqlite (918 sections): '/s/' signature lines (FY2023
# 10-K, the 8-Ks, the notes indenture, the 17 May 2024 SEC letter); 'Name, title' officer mentions (10-K, 10-Q,
# releases, 424B5, the Qorvo 8-K award table); the signature and service blocks ending 'Attorneys for ...' in the court
# filings (D.I. 15-618) and the docket's appearance entries; 'Dr./Ms./Mr. Name' in the orders and trial excerpts;
# 'Patent No(s).' in the releases and orders; the 8-K cover pages and the indenture's notice addresses.
# People are 'First/Nickname Last': any middle initials (with or without a full stop), a leading 'Judge', 'Mr.', 'Dr.'
# etc. and a trailing 'III'/'Jr.' are part of the match. The last name alone is replaced too, except for common
# surnames marked '*', which are replaced only after a first name or a title.
PEOPLE: dict[str, str] = {
    "the defendant's CEO": "Jeffrey/Jeff Shealy",
    "the defendant's CFO": "Kenneth/Ken Boller",
    "an executive of the defendant": "David/Dave Aichele; Tom Sepenzis; Rohan Houlden; Andrew/Drew Wright*",
    "a director of the defendant": "Arthur Geiss; Jerry Neal*; Steven DenBaars; Jeffrey McMahon; Suzanne Rudy*; "
                                   "Michelle Petock; Michael McGuire",
    "counsel for the defendant": "Stephen Brauerman; Ronald/Ron Golden*; Ronald Lemieux; David Elkins; Victoria Smith*; "
                                 "Xiaomei Cai; Rachael Harris*; Matthew Stanford*; David Jakopin; Dianne Sweeney*; "
                                 "Robert Fuhrer; David Stanton*; Theresa Roozen; Robert Perez/Prez*; Ryan Selness; Sean Jones*; "
                                 "Coleman Wombwell",
    "an expert witness for the defendant": "Carolyn/Carlyn Irwin; Michael Lebby; Clark Nguyen; Robert Darveaux",
    "the plaintiff's CEO": "Robert/Bob Bruggeworth",
    "an executive of the plaintiff": "Grant Brown*; Philip Chesley; Steven Creviston; Paul Fego",
    "an engineer of the plaintiff": "Robert Aigner; Helge Heinrich; Gernot Fattinger",
    "counsel for the plaintiff": "Jack Blumenfeld; Jeremy Tigan; Anthony David/Anthony Raucci; Robert Masters*; Jonathan DeFosse; "
                                 "Timothy Cremen; Trevor Quist; Eric Gill*; James Wald*; Kazim Naqvi; Roy Jung*; "
                                 "Kevin Ryan*; Thomas Carr*; Elodie Currier; Zachary Alper; Theodore Mayer*; "
                                 "Jennifer Klein/Jennifer Ayers",
    "a declarant for the plaintiff": "Kevin Faulkner*", "in-house counsel of the plaintiff": "Greg Warder",
    "a trial assistant for the plaintiff": "Faison",  # the transcript gives only 'Mr. Faison'
    "an expert witness for the plaintiff": "Melissa Bennis; Stanley Shanfield; John Bravman",
    "the judge": "Jon Phipps/Jon McCalla/McCal", "the earlier judge": "Richard Andrews*", "an officer of the trustee": "Lawrence Kusch",
}
# Names of entities: separators are loose ('Qorvo Inc', 'Qorvo, Inc.'), '&' and 'and' are interchangeable.
ENTITIES: dict[str, str] = {
    DEFENDANT: "Akoustis Technologies, Inc.; Akoustis Technologies; Akoustis, Inc.; Akoustis; AKTS",
    "the defendant's filer number": "1584754; 001-38029; 33-1229046",  # SEC CIK, file number, EIN
    "the defendant's subsidiary": "Grinding & Dicing Services, Inc.; Grinding & Dicing Services; GDSI; "
                                  "RFM Integrated Device, Inc.; RFM Integrated Device; RFMi",
    "the defendant's filter technology": "XBAW",
    "a university licensor of the defendant": "Cornell University; Cornell",
    "the development agency of the fab's county": "Ontario County Industrial Development Agency; OCIDA; Ontario County",
    "the defendant's packaging supplier": "Tai-Saw Technology Co., Ltd.; Tai-Saw Technology; Tai-Saw; TST",
    "counsel for the defendant": "Bayard, P.A.; Bayard; Pillsbury Winthrop Shaw Pittman LLP; Pillsbury; "
                                 "Squire Patton Boggs (US) LLP; Squire Patton Boggs; K&L Gates LLP; K&L Gates",
    PLAINTIFF: "Qorvo, Inc.; Qorvo US, Inc.; Qorvo US; Qorvo; Qoorvo; QRVO",  # 'Qoorvo': a docket typo
    "the plaintiff's filer number": "1604778; 001-36801; 46-5288992",
    "counsel for the plaintiff": "Morris, Nichols, Arsht & Tunnell LLP; Morris Nichols; "
                                 "Sheppard, Mullin, Richter & Hampton LLP; Sheppard Mullin",
    "the plaintiff v. the defendant": "Qorvo, Inc. v. Akoustis Technologies, Inc.; Qorvo v. Akoustis",
    "the trustee": "The Bank of New York Mellon Trust Company, N.A.; Bank of New York Mellon; BNY Mellon",
    "the case": "76727",  # the docket's id in court URLs
    "": "-JPM; -RGA; (JPM); (RGA); -JRG",  # judges' initials after a case number
}
PATENTS: dict[str, str] = {  # every format: '9,735,755', '9735755', 'U.S. Patent No. 9,735,755', "the '755 Patent"
    "the first patent in suit": "7,522,018", "the second patent in suit": "9,735,755",
    "the third patent in suit": "10,256,786", "the patent in the defendant's own suit": "7,250,360",
}
_STATE = r"(?:\s*,\s*(?:{}))?(?:\s+\d{{5}}(?:-\d{{4}})?)?"  # ', NC 28078' after a town
NC, NY = _STATE.format(r"NC|N\.C\.|North Carolina"), _STATE.format(r"NY|N\.Y\.|New York")
PATTERNS: list[tuple[str, str]] = [  # regular expressions (case-insensitive), in priority order
    (r"(?:C\.?\s?A\.?\s+)?(?:No\.?\s*)?(?:\d:)?21-(?:cv-)?0?1417(?:-[A-Z]{2,3})*", "the case"),
    (r"(?:\d:)?23-cv-0*180(?:-[A-Z]{2,3})*", "the defendant's own suit"),
    (r"9805\s+Northcross\s+Center\s+Court(?:\s*,?\s*Suite\s+A)?", "the defendant's headquarters address"),
    (r"\bHuntersville" + NC, "the defendant's headquarters town"),
    (r"\bCanandaigua" + NY, "the town of the defendant's wafer fab"),
    (r"7628\s+Thorndike\s+Road", "the plaintiff's headquarters address"),
    (r"\bGreensboro" + NC, "the plaintiff's headquarters town"),
    (r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "an email address"),
    (r"(?-i:\b\d{2,5}\s+(?:[NSEW]\.\s+|North\s+|South\s+|East\s+|West\s+)?(?:[A-Z][\w.]*\s+){1,3}"
     r"(?:Street|Road|Drive|Boulevard|Avenue|Parkway|Way|Lane|Court|Circle|Real)\b)", "an address"),
]


_TITLE = r"(?:(?:Judge|Hon|Honorable|Mr|Mrs|Ms|Dr|Professor|Prof)\.?\s+)"
_INITIAL = r"(?:[A-Z](?:\.\s*|\s+))"
_LEAD = r"(?:[A-Z]\.\s*)"


def _items(spec: str) -> list[str]:
    return [s.strip() for s in spec.split(";") if s.strip()]


def literal_pattern(alias: str) -> str:
    """A name as a pattern: loose separators, '&' = 'and', an optional final stop, word edges on letters."""
    toks = [t for t in re.split(r"[\s,]+", alias.strip()) if t]
    parts = [r"(?:&|and)" if t.lower() in ("&", "and") else
             re.escape(t.rstrip(".")) + (r"\.?" if t.endswith(".") else "") for t in toks]
    rx = r"\s*,?\s*".join(parts)
    if len(alias) > 5 or not alias[0].isalpha():  # a long name is found inside file names and addresses too
        return rx
    return rf"(?<![a-z]){rx}(?![a-z])"  # a short acronym ('AKTS', 'TST') only as a word


def person_pattern(spec: str) -> str:
    """'First/Nickname Last[*]': middle initials and a title optional; 'Last, First' (the docket's style) too; the
    last name alone unless marked '*'. A long last name is also found inside an e-mail or file name ('sbrauerman_')."""
    common = spec.endswith("*")
    first, _, last = spec.rstrip("*").rpartition(" ")
    if not first:  # the record gives only a title and a last name
        return rf"(?<![a-z]){_TITLE}?(?:{re.escape(last)})(?![a-z])"
    firsts = "|".join(re.escape(f) for f in first.split("/"))
    lasts = "|".join(re.escape(x) for x in last.split("/"))
    named = rf"(?:{_LEAD}*(?:{firsts})\s+{_INITIAL}{{0,3}})"
    suffix = r"(?:\s*,?\s*(?:III|II|Jr)(?![a-z])\.?)?"
    reverse = rf"(?<![a-z])(?:{lasts})\s*,\s*(?:{firsts})(?![a-z])(?:\s+{_INITIAL}{{0,3}})?"
    full = rf"(?<![a-z])(?:{_TITLE}{named}?|{named})(?:{lasts})(?![a-z]){suffix}"
    if common:
        return f"(?:{reverse}|{full})"
    edge = "" if min(map(len, last.split("/"))) >= 6 else r"(?<![a-z])"
    return rf"(?:{reverse}|{full}|{edge}(?:{lasts})(?![a-z]){suffix})"


def patent_pattern(number: str) -> str:
    """'U.S. Patent No. 9,735,755', '9735755', "the '755 Patent"."""
    digits = number.replace(",", "")
    full = ",?".join(re.escape(g) for g in number.split(","))
    return (rf"(?:\b(?:U\.?\s?S\.?\s+)?(?:Patent\s+)?(?:Nos?\.?\s*)?{full}\b"
            rf"|(?:\bthe\s+)?[’'‘`´]\s?{digits[-3:]}\b(?:\s+patent\b)?)")


def patterns() -> list[tuple[str, str]]:
    """Every (pattern, role) of the blocks above, most specific first."""
    out = [(patent_pattern(n), role) for role, n in PATENTS.items()]
    out.append((r"\b(?:U\.?\s?S\.?\s+)?Patent\s+Nos?\.?\s*\d{1,2},?\d{3},?\d{3}\b", "a patent"))
    out += [(person_pattern(p), role) for role, spec in PEOPLE.items() for p in _items(spec)]
    return out + PATTERNS


def aliases(disputes: list[DisputeInstance], borrower: str) -> dict[str, str]:
    """Alias -> role: the entity names above plus the instances' parties (and their first word)."""
    out = {a: role for role, spec in ENTITIES.items() for a in _items(spec)}
    for d in disputes:
        debtor = d.borrower_role == "debtor"
        mine, theirs = (DEFENDANT, PLAINTIFF) if debtor else (PLAINTIFF, "the defendant")
        for name, role in ((borrower, mine), (d.counterparty, theirs)):
            name = (name or "").strip()
            if len(name) >= 4:
                out.setdefault(name, role)
                first = re.split(r"[ ,]", name)[0]
                if len(first) >= 4:
                    out.setdefault(first, role)
        for f in d.financing:
            if f.issuer and len(f.issuer) >= 4:
                out.setdefault(f.issuer, DEFENDANT)
        ref = re.search(r"\d+:\d{2}-cv-\d+", d.order_reference or "")
        if ref:
            out.setdefault(ref.group(0), "the case")
    return out


def roles_for(disputes: list[DisputeInstance], borrower: str) -> Roles:
    """The replacement the recall check applies: the patterns (people, patents, case numbers, places) and the aliases."""
    return Roles(aliases(disputes, borrower), patterns())


class Roles:
    """Case-insensitive replacement of every alias and pattern by its role, applied to a whole Jev state. Patterns
    come first (in order), then the aliases longest first; the earliest match in the text wins."""

    def __init__(self, table: dict[str, str], extra: list[tuple[str, str]] = ()) -> None:
        pairs = list(extra) + [(literal_pattern(k), v) for k, v in sorted(table.items(), key=lambda kv: -len(kv[0]))]
        self.roles = [v for _, v in pairs]
        self.rx = re.compile("|".join(f"({p})" for p, _ in pairs), re.IGNORECASE)

    def text(self, s: str) -> str:
        for _ in range(3):  # a role never contains an alias; repeat only for overlaps a replacement exposes
            new = self.rx.sub(lambda m: self.roles[m.lastindex - 1], s)
            if new == s:
                break
            s = new
        return s

    def __call__(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {self(k) if isinstance(k, str) else k: self(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self(v) for v in obj)
        return obj


def change(a: dict[str, float], b: dict[str, float]) -> float:
    """Largest absolute change in any branch (probability units)."""
    return max(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


async def ask_roles(fc: Forecaster, judge: ForecastJudge, roles: Roles) -> dict[str, dict[str, float]]:
    """Every forecast node's distribution under role names (the same questions, branches and path facts)."""
    async def one(n):
        st, fids, _ = fc.state(n)
        o = await judge.forecast(n.question_id, roles(st), (n.instance_id, *fids), n.branches)
        return n.key, answer_distribution(n.key, n.branches, o)

    return dict(await asyncio.gather(*(one(n) for n in fc.nodes.values())))


def recall_check(fc: Forecaster, judgments: dict[str, Judgment], judge: ForecastJudge, borrower: str) -> dict:
    """Per node {original, roles, max_change}; summary over the questions. `judgments` is not modified."""
    roles = roles_for(fc.disputes, borrower)
    role_dist = asyncio.run(ask_roles(fc, judge, roles))
    nodes = {}
    for k, j in judgments.items():
        r = role_dist[k]
        nodes[k] = {"original": dict(j.distribution), "roles": r, "max_change": round(change(j.distribution, r), 6)}
    return {"nodes": nodes, "summary": summarize(nodes, judgments)}


def summarize(nodes: dict[str, dict], judgments: dict[str, Judgment]) -> dict:
    ch = [v["max_change"] for v in nodes.values()]
    moved = sorted(((k, v) for k, v in nodes.items() if v["max_change"] > MOVED), key=lambda kv: -kv[1]["max_change"])
    return {"questions": len(ch), "mean_abs_change": round(sum(ch) / len(ch), 6) if ch else 0.0,
            "max_abs_change": max(ch, default=0.0), "threshold": MOVED,
            "moved": [{"key": k, "question_id": judgments[k].question_id, "event": judgments[k].event,
                       "original": v["original"], "roles": v["roles"], "max_change": v["max_change"]}
                      for k, v in moved]}
