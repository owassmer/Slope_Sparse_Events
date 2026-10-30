"""The parties named by their roles in what the judgment model reads (Owen, 30 October).

A forecast is the model's judgment from the record as of the review date. Told who the parties are, the model can
draw on what it knows of them, including what happened after the review date, which no instruction can be relied on
to keep out; named by their roles, it judges from the record. Asked both ways, real questions moved by up to 0.3 with
the names left in, and most questions the judge declined were answered without them (29-30 October).

What is replaced is what the case declares (`cases/<case>/scenario.json` "parties": each a role and the names that
refer to it: the party's legal and short names and their misspellings in transcripts, its ticker, its insiders, its
products and licensors, the docket). It removes the direct cues to who the parties are; it does not make a public
case unrecognisable from its substance, and third parties (courts, counsel, experts, underwriters) keep their names.
The page keeps every name: only the judge's input is changed, at its boundary (`DisputeProfile.forecast`).

Only text is changed (string values: never a key, a number or a boolean). Names are matched as declared, and in
capitals, as whole words; the longest declared name first, across all roles (a claimant called "Alpha Holdings" is
not read as "Alpha"). A role keeps the grammar around it: "the Akoustis defendants" -> "the Company defendants",
"Akoustis' subsidiary" -> "the Company's subsidiary", a role that opens a sentence or quotation starts with a capital,
and a name ending in a period that also ended a sentence leaves the period. Where a party's own document defines "the
Company" as itself, the defined term becomes that party's role (`author`), so a claimant's filing never reads as the
company's.
"""
from __future__ import annotations

import re
from collections.abc import Callable


class NoParties(ValueError):
    """The case declares no parties: its names would reach the judge."""


class Parties:
    """A case's declared parties (`scenario.json` "parties": [{"as": role, "names": [...], "placeholder": ...}])."""

    def __init__(self, declared: list[dict] | None) -> None:
        if not declared:
            raise NoParties('the case declares no parties (cases/<case>/scenario.json "parties")')
        self.by_name: dict[str, str] = {}
        for p in declared:
            for n in p["names"]:
                for form in {n, n.upper()}:
                    if form in self.by_name and self.by_name[form] != p["as"]:
                        raise ValueError(f"{form!r} is declared for two roles")
                    self.by_name[form] = p["as"]
        self.placeholders = {p["placeholder"]: p["as"] for p in declared if p.get("placeholder")}
        self.company = self.placeholders.get("company")
        self.organisations = set(self.placeholders.values())  # parties whose own documents define "the Company"
        # a name's spaces match any whitespace (a source's line breaks are kept verbatim)
        alternatives = "|".join(re.escape(n).replace(r"\ ", r"\s+")
                                for n in sorted(self.by_name, key=len, reverse=True))
        self.pattern = re.compile(rf"(?P<the>\b[Tt]he\s+)?(?<![A-Za-z0-9])(?P<name>{alternatives})(?![A-Za-z0-9])"
                                  r"(?P<poss>[’'](?=\s+[A-Za-z]))?")

    def role(self, matched: str) -> str:
        return self.by_name[re.sub(r"\s+", " ", matched)]

    def text(self, s: str) -> str:
        def one(m: re.Match) -> str:
            role = self.role(m.group("name"))
            article = re.match(r"(the|an?)\s+", role, re.IGNORECASE)
            if m.group("the") and article:  # "the Akoustis defendants": one article, the text's own
                role = m.group("the") + role[article.end():]
            elif m.group("the"):
                role = m.group("the") + role
            opened = m.start() > 0 and s[m.start() - 1] in "'‘"  # a quotation the apostrophe closes
            if m.group("poss") and not opened:
                role += "’s"
            elif m.group("poss"):
                role += m.group("poss")
            end = m.end()
            if m.group("name").endswith(".") and (end == len(s) or (s[end:end + 1].isspace() and
                                                                   s[end + 1:end + 2][:1].isupper())):
                role += "."  # the name's period also ended the sentence
            return role

        return self.capitalise(self.pattern.sub(one, s))

    def capitalise(self, out: str) -> str:
        for role in set(self.by_name.values()):  # a role that opens a sentence or a quotation
            if role[:1].islower():
                out = re.sub(rf'(^|[.!?:;]\s+|["“‘(\[]\s*|(?:^|\s)\'|\n\s*)({re.escape(role)})',
                             lambda m: m.group(1) + m.group(2)[0].upper() + m.group(2)[1:], out)
        return out

    def role_of(self, s: str) -> str | None:
        """The one declared party a text (a source's author) names (None where it names none, or several)."""
        found = {self.role(m.group("name")) for m in self.pattern.finditer(s or "")}
        return found.pop() if len(found) == 1 else None


def _walk(obj, fn: Callable[[str], str], author_role: str | None, parties: Parties):
    if isinstance(obj, str):
        if author_role in parties.organisations and author_role != parties.company:
            # the author's own defined term first: in its document, "the Company" is the author itself
            term = parties.company[4:] if parties.company.lower().startswith("the ") else parties.company
            obj = re.sub(rf"\b([Tt]he)\s+{re.escape(term)}\b",
                         lambda m: (author_role[0].upper() if m.group(1) == "The" else author_role[0]) + author_role[1:],
                         obj)
        return fn(obj)
    if isinstance(obj, list):
        return [_walk(x, fn, author_role, parties) for x in obj]
    if isinstance(obj, dict):
        # whose document an item quotes: its author, else its source's title (a party's own filing)
        own = next((obj[k] for k in ("author", "source", "title") if isinstance(obj.get(k), str)), None)
        role = parties.role_of(own) if own is not None else author_role
        out = {}
        for k, v in obj.items():
            out[k] = _walk(v, fn, None if k in ("author", "source", "title") else role, parties)
        return out
    return obj


def apply(obj, declared: list[dict] | None):
    """`obj` (the judge's state: any JSON value) with every declared name replaced by its party's role."""
    parties = Parties(declared)
    return _walk(obj, parties.text, None, parties)


def fill(template: str, declared: list[dict] | None) -> str:
    """A registry template's party placeholders ({company}, {claimant}) filled with the parties' roles."""
    parties = Parties(declared)
    for key, role in parties.placeholders.items():
        template = template.replace("{" + key + "}", role)
    return parties.capitalise(template)


def residue(obj, declared: list[dict] | None) -> list[str]:
    """The declared names still present in `obj`'s text (none, after `apply`)."""
    parties = Parties(declared)
    found: set[str] = set()

    def look(o):
        if isinstance(o, str):
            found.update(m.group("name") for m in parties.pattern.finditer(o))
        elif isinstance(o, list):
            for x in o:
                look(x)
        elif isinstance(o, dict):
            for v in o.values():
                look(v)

    look(obj)
    return sorted(found)
