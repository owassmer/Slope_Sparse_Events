"""The parties named by their roles in what the judgment model reads (Owen, 30 October).

A forecast is the model's judgment from the record as of the review date. Told who the parties are, the model can
draw on what it knows of them, including what happened after the review date, which no instruction can be relied on
to keep out; named by their roles ("the Company", "the Claimant"), it judges from the record. Asked both ways, real
questions moved by up to 0.3 when the names were left in (29-30 October). So every name the case declares for a
party, and its identifiers (its ticker, the docket number), is replaced by the role the case gives it, in everything
the judge reads: the question, the situation, the evidence and the case block. The page keeps the names: only the
judge's input is changed, at its boundary (`DisputeProfile.forecast`).

The names are case inputs (`cases/<case>/scenario.json` "parties": role -> the names and identifiers that refer to
the party); a case that declares none cannot be asked (the names would reach the judge unseen).
"""
from __future__ import annotations

import json
import re


class NoParties(ValueError):
    """The case declares no parties: its names would reach the judge."""


def _pattern(names: list[str]) -> re.Pattern:
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # a name, not part of a longer word; a bare trailing apostrophe is its possessive
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alternatives})(?![A-Za-z0-9])([’'](?!s))?", re.IGNORECASE)


def _sentence_case(text: str, roles: list[str]) -> str:
    """A role that opens a sentence or a quoted phrase starts with a capital ("The Company files ...")."""
    for role in roles:
        if role.startswith("the "):
            text = re.sub(rf'(^|[.!?:]\s+|"\s*|\n\s*)({re.escape(role)})',
                          lambda m: m.group(1) + m.group(2)[0].upper() + m.group(2)[1:], text)
    return text


def apply(obj, parties: dict[str, list[str]]):
    """`obj` (the judge's state: any JSON value) with every declared name replaced by its party's role."""
    if not parties:
        raise NoParties("the case declares no parties (cases/<case>/scenario.json \"parties\")")
    text = json.dumps(obj, ensure_ascii=False)
    for role, names in parties.items():
        text = _pattern(names).sub(lambda m, role=role: role + ("'s" if m.group(1) else ""), text)
    text = _sentence_case(text, list(parties))
    return json.loads(text)


def residue(obj, parties: dict[str, list[str]]) -> list[str]:
    """The declared names still present in `obj` (none, after `apply`)."""
    text = json.dumps(obj, ensure_ascii=False)
    return sorted({m.group(0) for names in parties.values() for m in _pattern(names).finditer(text)})
