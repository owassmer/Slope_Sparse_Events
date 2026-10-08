"""Conditional forecasting (dispute model 4.0.0): the chain templates walked for each interpreted dispute, the
residual questions Jev answers, and the composition of its answers along each path.

A path is a sequence of steps along the chains (federal post-judgment procedure, the indenture, bankruptcy effects).
Each step carries one edge: a residual Jev node, or a composite of several whose probability code computes by the
chain rule (settlement = offer x accept; a stay = motion x approval; a petition on the notes = the holders act x (the
issuer files, or else the holders file); the post-trial ruling = the merits answers multiplied along each outcome and
summed within its amount class). Timing, amounts and affordability are code: dates are drawn per trajectory,
independently of every probability (app/analysis/events.py), and the path facts each question is given are simulated
before Jev is asked. Only impossibility changes structure: an occurred event sets the stage, a payment ends the
dispute, a decision that can fall inside the horizon on no trajectory of the path is not asked (its window is closed),
and arithmetic removes 'pay' only where the amount exceeds available cash on every trajectory of the path.

Jev nodes are keyed by the facts their question depends on (interval, amount class, stay and notes status), so paths
that share those facts share one judgment; the path facts are pooled over the paths that reach the node.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import itertools
import json
import math
import pickle
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Protocol

import numpy as np
from zlib_ng import zlib_ng

from app.disputes.rules import load_model
from app.domain.investigation import AtomicFinding, DisputeInstance, SemanticObservation
from app.domain.values import usd

COMPOSITE = "="
CLASS_TAG = "#"  # a question's situation class in its key (`situation_class`)
ROWS_OFF = __import__("os").environ.get("SLOPE_ROWS") == "0"  # `_Walk._rows`: every trace on every draw


class ForecastJudge(Protocol):
    async def forecast(self, question_id: str, state: dict, subject_ids: tuple[str, ...],
                       branches: tuple[str, ...] | None = None) -> SemanticObservation: ...


@dataclass(frozen=True)
class Node:
    key: str  # instance:node|context
    instance_id: str
    node: str
    context: str
    cls: str
    question_id: str
    event: str
    assumptions: tuple[str, ...]
    window: str
    branches: tuple[str, ...]


@dataclass
class Judgment:
    """Jev's conditional judgment at one node: the full distribution over its branches."""
    key: str
    instance_id: str
    node: str
    question_id: str
    event: str
    assumptions: tuple[str, ...]
    window: str
    distribution: dict[str, float]  # branch -> probability (Noul: yes = value, no = 1 - value)
    confidence: float | None = None
    finding_ids: tuple[str, ...] = ()
    readings: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    observation_id: str = ""
    path_facts: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DisputePath:
    """One path through one dispute's chains: the steps taken (node, context, branch), its outcome, and the edges
    that carry its probability (node key or composite key, branch)."""
    instance_id: str
    steps: tuple[tuple[str, str, str], ...]
    outcome: str
    edges: tuple[tuple[str, str], ...]
    cls: str = ""
    # the trajectories the path follows (np.packbits of a bool per draw; None: every draw): a question asked of one
    # option group forks the path once per (group, answer), each child on its group's trajectories (Owen's ruling on
    # within-path grouping, 29 Sep 2026); its probability is the product of its edges, and each draw's paths weigh 1
    mask: bytes | None = None
    # per classed question on the path (`Forecaster.class_key`): (question key, its class tags, and per draw the path
    # follows, the index of its tag, int8 bytes; -1 where the question is not live there; None: one tag throughout)
    classes: tuple = ()


def pack_mask(m: np.ndarray | None) -> bytes | None:
    return None if m is None else np.packbits(m).tobytes()


def path_mask(p: DisputePath, n: int) -> np.ndarray | None:
    """The draws a path follows, [n] bool (None: every draw)."""
    return None if p.mask is None else np.unpackbits(np.frombuffer(p.mask, dtype=np.uint8), count=n).astype(bool)


def combo_mask(combo, n: int) -> np.ndarray | None:
    """The draws a joint path follows: those every one of its paths follows (None: every draw)."""
    out = None
    for p in combo:
        m = path_mask(p, n)
        if m is not None:
            out = m if out is None else out & m
    return out


def fmt(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def composite(conjunctions: list[list[tuple[str, str]]]) -> str:
    """A composite edge: P(yes) = sum over disjoint conjunctions of the product of their (node, branch) answers."""
    return COMPOSITE + json.dumps([[list(a) for a in c] for c in conjunctions], separators=(",", ":"))


def atoms(key: str) -> set[str]:
    return {k for c in json.loads(key[1:]) for k, _ in c} if key.startswith(COMPOSITE) else {key}


@functools.cache
def _conjunctions(key: str) -> tuple:
    """A composite key's disjoint conjunctions of (node, branch), parsed once."""
    return tuple(tuple(tuple(e) for e in c) for c in json.loads(key[1:]))


class Dist(dict):
    """Node key -> branch distribution; composite keys are computed on demand by the chain rule."""

    def __missing__(self, key: str) -> dict[str, float]:
        if not key.startswith(COMPOSITE):
            raise KeyError(key)
        p = sum(math.prod(self[k][b] for k, b in c) for c in _conjunctions(key))
        p = min(max(p, 0.0), 1.0)
        self[key] = v = {"yes": p, "no": 1.0 - p}
        return v


def path_probability(edges: tuple[tuple[str, str], ...], dist: dict[str, dict[str, float]]) -> float:
    p = 1.0
    for key, branch in edges:
        p *= dist[key][branch]
    return p


def joint_paths(per: dict[str, dict[str, list[DisputePath]]], order: list) -> list[tuple[DisputePath, ...]]:
    """Every combination of the disputes' paths (disputes are independent in 4.0.0; see `Forecaster.ordered`)."""
    combos: list[tuple[DisputePath, ...]] = [()]
    for d, _parent in order:
        combos = [combo + (p,) for combo in combos for p in per[d.instance_id][""]]
    return combos


def combo_probability(combo: tuple[DisputePath, ...], dist: dict[str, dict[str, float]]) -> float:
    return path_probability(tuple(itertools.chain.from_iterable(p.edges for p in combo)), dist)


def distributions(judgments: dict[str, Judgment], overrides: dict[str, dict[str, float]] | None = None) -> Dist:
    """Node key -> branch distribution, with any overrides applied; composites follow from their parts."""
    out = Dist({k: dict(j.distribution) for k, j in judgments.items()})
    for k, v in (overrides or {}).items():
        if k in out:
            out[k] = dict(v)
    return out


def neutral_map(judgments: dict[str, Judgment]) -> dict[str, dict[str, float]]:
    """Attribution step 2: every residual neutral (a Noul at 0.5, a Choice uniform over the branches arithmetic left
    it); composites and everything the record fixes follow unchanged."""
    return {k: {b: 1 / len(j.distribution) for b in j.distribution} for k, j in judgments.items()}



def answer_distribution(key: str, branches: tuple[str, ...], o: SemanticObservation) -> dict[str, float]:
    """Jev's answer at one node as a distribution over its branches (a Noul: yes = value; a Choice: renormalised
    over the branches arithmetic left the node)."""
    if len(branches) == 2 and (o.primitive == "noul" or branches == ("yes", "no")):
        # A Noul: the first branch is the question's 'true' ('yes', 'granted'), the second its negation.
        if o.noul_value is None:
            raise RuntimeError(f"Jev returned no probability for {key}")
        return {branches[0]: float(o.noul_value), branches[1]: 1.0 - float(o.noul_value)}
    probs = {k: float(v) for k, v in (o.probabilities or {}).items() if k in branches}
    total = sum(probs.values())
    if total <= 0:
        raise RuntimeError(f"Jev returned no distribution for {key}")
    return {k: probs.get(k, 0.0) / total for k in branches}


def state_evidence(st: dict) -> list:
    """The evidence a Jev state carries, for its Judgment: one list (20 Jun), or the 14 May state's three parts."""
    if "evidence" in st:
        return st["evidence"]
    return [e for k in ("historical_evidence", "party_assertions", "court_findings") for e in st.get(k, ())]


def load_registry() -> dict:
    from app.config import question_registry

    return question_registry()


def registry_entry(qid: str) -> dict:
    return next((q for q in load_registry()["questions"] if q["id"] == qid), {})


def _q(model: dict) -> dict[str, dict]:
    """Chain node name -> its template spec (all templates)."""
    return {n: s for t in model["templates"].values() for n, s in t.get("nodes", {}).items()}


_ROW_BLOBS: dict[bytes, bytes] = {}  # identical rows share one compressed blob


_ROW_FILL = {"day": 10**6, "groups": -1}  # day: events.BIG (asserted in pack_row); elsewhere 0 (never read)


class _Kept:
    """A per-trajectory array of a recorded row, on the row's stored trajectories only (`pack_row`)."""
    __slots__ = ("a",)

    def __init__(self, a: np.ndarray) -> None:
        self.a = a


def _keep(v, on: np.ndarray, n: int):
    if isinstance(v, np.ndarray) and v.ndim >= 1 and v.shape[0] == n:
        return _Kept(v[on])
    if type(v) is dict:
        return {k: _keep(x, on, n) for k, x in v.items()}
    if type(v) in (list, tuple):
        return type(v)(_keep(x, on, n) for x in v)
    return v


def _whole(v, ix: np.ndarray, n: int, name: str = ""):
    if isinstance(v, _Kept):
        out = np.full((n, *v.a.shape[1:]), _ROW_FILL.get(name, 0) if v.a.dtype != object else None, dtype=v.a.dtype)
        out[ix] = v.a
        return out
    if type(v) is dict:
        return {k: _whole(x, ix, n, k) for k, x in v.items()}
    if type(v) in (list, tuple):
        return type(v)(_whole(x, ix, n, name) for x in v)
    return v


# A question's facts as of its decision day (QUESTIONS_20240514 §1 Two dates: the state holds only events reached on
# the path before the decision date). A recorded row is read off a trace that can hold events dated after the
# decision on a trajectory: a later-walked step, or the whole path for a state-triggered decision. Those events have
# not happened there. Scheduled dates (the post-trial ruling's, the entry, the judgment default's availability, the
# holders' route) are terms known at the decision and stay; an outcome dated after the decision is removed.
OUTCOME_DAYS = ("delisted", "stayed_from", "notes_due_day", "nonpayment_day", "first_unpaid")
OUTCOME_TRIGGERS = {"holders_petition_earliest": ("marks", "notes_due"), "repurchase_due": ("sit", "delisted")}


def as_of(row: dict) -> dict:
    """The row as of its decision day on each trajectory (`OUTCOME_DAYS`): an outcome dated after the decision day
    reads as not having happened (BIG; a petition -1; the marks BIG), an offering initiated after it is not there
    and one closing after it has not closed, and a trigger that exists only because of such an outcome is absent."""
    from app.analysis.events import BIG

    day = row.get("day")
    if not isinstance(day, np.ndarray):
        return row
    n = day.shape[0]
    per = lambda v: isinstance(v, np.ndarray) and v.shape[:1] == (n,)  # noqa: E731
    later = lambda v: np.asarray(v) > day  # noqa: E731
    out = dict(row)
    marks, sit = row.get("marks") or {}, row.get("sit") or {}
    if per(row.get("petition")):
        out["petition"] = np.where(later(row["petition"]), -1, row["petition"]).astype(row["petition"].dtype)
    if marks:
        out["marks"] = {k: np.where(later(v), BIG, v).astype(v.dtype) if per(v) else v for k, v in marks.items()}
    trig = row.get("triggers")
    if trig:
        t2 = dict(trig)
        for name, (where, what) in OUTCOME_TRIGGERS.items():
            src = (marks if where == "marks" else sit).get(what)
            if name in t2 and per(t2[name]) and per(src):
                t2[name] = np.where(later(src), BIG, t2[name]).astype(t2[name].dtype)
        if per(sit.get("motions_filed")) and "appeal_deadline" in t2:
            filed = sit["motions_filed"] <= day
            ruled = sit["ruling"] <= day
            t2["appeal_deadline"] = np.where(
                filed, np.where(ruled, t2["appeal_deadline"], BIG), sit["entry_appeal_deadline"])
        out["triggers"] = t2
    if sit:
        s2 = dict(sit)
        if per(sit.get("motions_filed")):
            s2["motions_filed"] = np.where(sit["motions_filed"] <= day, sit["motions_filed"], BIG)
            s2["ruling"] = np.where(sit["motions_filed"] <= day, sit["ruling"], BIG)
        for k in OUTCOME_DAYS:
            if per(sit.get(k)):
                s2[k] = np.where(later(sit[k]), BIG, sit[k]).astype(sit[k].dtype)
        if per(sit.get("notes_due_how")) and per(sit.get("notes_due_day")):
            s2["notes_due_how"] = np.where(later(sit["notes_due_day"]), "", sit["notes_due_how"])
        if per(sit.get("route_days")) and per(sit.get("notes_due_day")):  # the route applies once the notes are due
            s2["route_days"] = np.where(later(sit["notes_due_day"]), -1, sit["route_days"]).astype(
                sit["route_days"].dtype)
        if isinstance(sit.get("offerings"), list):
            offs = []
            for o in sit["offerings"]:
                init, close, closed = (np.broadcast_to(np.asarray(x), (n,)) for x in o)
                gone = init > day
                offs.append((np.where(gone, BIG, init).astype(np.asarray(o[0]).dtype),
                             np.where(gone, BIG, close).astype(np.asarray(o[1]).dtype),
                             np.asarray(closed, dtype=bool) & ~gone & (close <= day)))
            s2["offerings"] = offs
        out["sit"] = s2
    return out


def situation_class(row: dict, live: np.ndarray | None = None, *, appeal: bool = True,
                    stay: bool = False) -> np.ndarray | None:
    """Per trajectory, the question's situation class as of its decision day (QUESTIONS_20240514 §1 Grouping: a
    difference in legal status, available actions or ability to pay splits the group; each dimension is a fact the
    state gives): the judgment's band and standing, the notes' status, the listing, the offering's availability (or
    why it is unavailable), whether cash covers the amount owed, and dated appeal status, as one tag. '' where the question is not live on
    the trajectory (`live`: the question's own rule, `Forecaster.live`; else dated before the horizon's end and any
    petition); None where the row has no question-state snapshot (a 20 Jun question)."""
    from app.analysis.events import BIG
    from app.disputes.state14 import appeal_state

    s, day = row.get("sit"), row.get("day")
    if not isinstance(s, dict) or not isinstance(day, np.ndarray):
        return None
    n = day.shape[0]
    if stay and (not isinstance(s.get("stay_status"), np.ndarray) or s["stay_status"].shape != (n,)):
        raise ValueError("Question consuming judgment status requires a dated stay-status snapshot")
    pet = np.where(row["petition"] < 0, BIG, row["petition"])
    live = np.flatnonzero((day < BIG) & (day < pet) if live is None else live)

    def arr(k, fill):
        v = s.get(k)
        return v if isinstance(v, np.ndarray) and v.shape[:1] == (n,) else np.full(n, fill)
    band = s.get("band")
    band = "-" if band is None or not isinstance(band, str) else band
    standing = arr("standing", "none")
    due, avail, dl, npd = (arr(k, BIG) for k in ("notes_due_day", "default_available", "delisted", "nonpayment_day"))
    notes = np.where(due <= day, "due", np.where((avail <= day) | (dl <= day) | (npd <= day), "default", "current"))
    listing = arr("listing", "listed")
    pending, ledger = arr("offering_pending", False).astype(bool), arr("ledger", 1)
    net = arr("offer_available", 1)  # the offering the company would initiate (0: its proceeds short of the shortfall)
    offer = np.where(listing == "delisted", "delisted", np.where(pet <= day, "petition", np.where(
        pending, "pending", np.where(ledger <= 0, "nocapacity", np.where(net <= 0, "insufficient", "available")))))
    pay = np.where((row["owed"] > 0) & (row["cash"] >= row["owed"]), "pay", "nopay")
    out = np.full(n, "", dtype=object)
    if live.size:
        tag = np.full(live.size, f"{CLASS_TAG}{band}")
        for part in (standing, notes, listing, offer, pay):
            tag = np.strings.add(np.strings.add(tag, "."), np.asarray(part)[live].astype(str))
        if appeal:
            tag = np.strings.add(np.strings.add(tag, ".appeal"), appeal_state(row)[live].astype(str))
        if stay:
            tag = np.strings.add(np.strings.add(tag, ".stay"), s["stay_status"][live].astype(str))
        out[live] = tag.astype(object)
    return out


def group_classes(cls: np.ndarray | None, groups: np.ndarray) -> np.ndarray:
    """A grouped question's classes (Owen's ruling on within-path grouping, 29 Sep 2026, carried per draw as
    QUESTIONS §1 Grouping carries situations): each draw where it is asked (groups >= 0) in its situation class and
    its option group ('.g<group>'), so each class is one question with the group's own answers."""
    out = np.full(len(groups), "", dtype=object)
    j = np.flatnonzero(groups >= 0)
    if j.size:
        base = np.full(j.size, f"{CLASS_TAG}-", dtype=object)
        if cls is not None:
            c = cls[j]
            base = np.where(c != "", c, base)
        tag = np.strings.add(np.strings.add(base.astype(str), ".g"), groups[j].astype(np.int64).astype(str))
        out[j] = tag.astype(object)
    return out


def qcls_best(entries, steps: tuple) -> np.ndarray | None:
    """Of a question's recorded classes [(prefix, classes)], the record whose prefix the path `steps` shares
    furthest (the latest of equals)."""
    best, bl = None, -1
    for at, cls in entries:
        n = 0
        if at is not None:
            while n < min(len(at), len(steps)) and at[n] == steps[n]:
                n += 1
        if n >= bl:
            best, bl = cls, n
    return best


def class_entry(k: str, cls: np.ndarray, mask: np.ndarray | None) -> tuple:
    """A path's `classes` entry for question k from its per-draw classes, on the draws the path follows."""
    c = cls if mask is None else cls[mask]
    tags = tuple(sorted({x for x in c if x}))
    if len(tags) == 1 and all(c):  # one class, live on every draw
        return k, tags, None
    return k, tags, np.array([tags.index(x) if x else -1 for x in c], dtype=np.int8).tobytes()


def _rewrite(key: str, to: dict) -> str:
    """A node or composite key with its question keys replaced (`to`: key -> class key)."""
    if not key.startswith(COMPOSITE):
        return to.get(key, key)
    return composite([[(to.get(k, k), b) for k, b in c] for c in _conjunctions(key)])


def class_firsts(known, dead=frozenset()) -> dict:
    """Fallbacks for inactive decisions, with dead classes retaining their exact answer domain.

    Base keys serve unclassified inactive draws; full dead keys select a class
    offering the same answers. With dead classes, known must include node metadata.
    """
    first = {}
    for k in sorted(known):  # a question live on no path at all reads its first class (it moves no figure either)
        if "|" + CLASS_TAG in k:
            base = k.split("|" + CLASS_TAG)[0]
            if k not in dead or first.get(base, k) in dead:
                first[base] = k if base not in first or first[base] in dead else first[base]
    if dead:
        if not isinstance(known, Mapping):
            raise ValueError("Dead-class substitution requires question answer domains")
        domains = {}
        for k in sorted(known):
            if "|" + CLASS_TAG not in k:
                continue
            base = k.split("|" + CLASS_TAG)[0]
            domain = base, tuple(known[k].branches)
            if domain not in domains or domains[domain] in dead and k not in dead:
                domains[domain] = k
        for k in dead:
            base = k.split("|" + CLASS_TAG)[0]
            first[k] = domains[base, tuple(known[k].branches)]
    if isinstance(known, Mapping):
        # By answer set: the class an inactive draw reads must offer exactly the answers the path's sibling paths
        # take there (`expand_classes`), or the draw's answers do not sum to one (or the path's own answer is
        # missing). ("by", base, answers) -> a class offering those answers (live first, then the first by key);
        # ("full", base) -> every answer any class of the question offers.
        for k in sorted(known):
            if "|" + CLASS_TAG not in k:
                continue
            base = k.split("|" + CLASS_TAG)[0]
            answers = frozenset(known[k].branches)
            at = ("by", base, answers)
            if at not in first or (first[at] in dead and k not in dead):
                first[at] = k
            first[("full", base)] = first.get(("full", base), frozenset()) | answers
    return first


def inactive_class(k: str, tags: tuple, known, first: dict) -> str:
    """The class tag a path's draws read for question k where its class record says the question is not live
    there: a class offering the answers of the path's live classes (`tags`), so that the sibling paths' answers on
    those draws sum to one; with no live class on the path, the class offering every answer. The answer books
    nothing on those draws, so which class of that answer set is read does not change any figure."""
    want = (frozenset().union(*(known[f"{k}|{t}"].branches for t in tags)) if tags
            else first.get(("full", k))) if isinstance(known, Mapping) else None
    return first.get(("by", k, want), first[k]).rsplit("|", 1)[1]


def expand_classes(paths: list, known, n: int, dead=frozenset(), first: dict | None = None) -> list:
    """Each path split by its trajectories' question classes (`DisputePath.classes`): one path per combination of
    classes its draws fall in, on those draws, each question key replaced by its class's. A draw where a question is
    not live reads the question's first class asked anywhere (`known`: node keys; `first`: `class_firsts`, computed
    here when not given), on every path: its answer books nothing inside the horizon there, so any class gives the
    same result, and one class for all of the question's branches keeps each draw's answers summing to one. A class
    live on no path of the tree (`dead`: the facts found no live trajectory for it) instead retains its saved
    answer domain when selecting a replacement, everywhere.
    n: the draws."""
    if first is None:
        first = class_firsts(known, dead)
    out = []
    for p in paths:
        if not p.classes:
            out.append(p)
            continue
        m = path_mask(p, n)
        on = np.arange(n) if m is None else np.flatnonzero(m)
        cols, fixed = [], {}
        for k, tags, codes in p.classes:
            if dead:
                # Keep the saved answer domain: replacing file/neither with
                # offering/file/neither loses mass even when the decision is inactive.
                tags = tuple(first[f"{k}|{t}"].rsplit("|", 1)[1]
                             if f"{k}|{t}" in dead else t for t in tags)
            if not tags:
                if k in first:
                    fixed[k] = first[k]
            elif codes is None:
                fixed[k] = f"{k}|{tags[0]}"
            else:
                c = np.frombuffer(codes, dtype=np.int8).astype(np.int64)
                if (c < 0).any():  # not live on these draws: a class offering the path's answers there
                    t0 = inactive_class(k, tags, known, first)
                    tags = tags if t0 in tags else (*tags, t0)
                    c = np.where(c < 0, tags.index(t0), c)
                cols.append((k, tags, c))
        if not cols:
            out.append(replace(p, edges=tuple((_rewrite(e, fixed), b) for e, b in p.edges), classes=()))
            continue
        uniq, inv = np.unique(np.stack([c for *_, c in cols], axis=1), axis=0, return_inverse=True)
        inv = np.asarray(inv).ravel()
        for u in range(len(uniq)):
            sm = np.zeros(n, dtype=bool)
            sm[on[inv == u]] = True
            to = {**fixed, **{k: f"{k}|{tags[int(c)]}" for (k, tags, _), c in zip(cols, uniq[u], strict=True)}}
            out.append(replace(p, edges=tuple((_rewrite(e, to), b) for e, b in p.edges), mask=pack_mask(sm),
                               classes=()))
    return out


def pack_row(row: dict, cache: dict | None = None) -> bytes:
    """A recorded row as one compressed pickle, interned by content (identical rows are stored once). Only the
    trajectories where the question can be live are stored: its decision is dated (not BIG) and precedes any petition
    (`Forecaster.live` reads no other); `unpack_row` restores the others as BIG days and zeros, which no read takes.
    On those trajectories a trigger that never falls (BIG) is left out, as `_contract_dates` reads it, and option
    groups asked of none of them are None, as `walk_groups` reads them: one stored form whatever the other
    trajectories hold."""
    from app.analysis.events import BIG

    assert _ROW_FILL["day"] == BIG
    day, pet = row.get("day"), row.get("petition")
    if isinstance(day, np.ndarray) and isinstance(pet, np.ndarray):
        n = day.shape[0]
        on = (day < BIG) & ((pet < 0) | (day < pet))
        trig, g = row.get("triggers"), row.get("groups")
        if trig:
            row = {**row, "triggers": {k: trig[k] for k in sorted(trig) if not (
                isinstance(trig[k], np.ndarray) and trig[k].shape[:1] == (n,) and (trig[k][on] >= BIG).all())}}
        if isinstance(g, np.ndarray) and g.shape[:1] == (n,) and not (g[on] >= 0).any():
            row = {**row, "groups": None}
        sit = row.get("sit")
        if isinstance(sit, dict) and isinstance(sit.get("offerings"), list):  # never initiated here: absent
            row = {**row, "sit": {**sit, "offerings": [o for o in sit["offerings"] if not (
                isinstance(o[0], np.ndarray) and o[0].shape[:1] == (n,) and (o[0][on] >= BIG).all())]}}
        row = {"__ix__": np.flatnonzero(on).astype(np.int32), "__n__": n,
               **{k: _keep(v, on, n) for k, v in row.items()}}
    raw = pickle.dumps(row, protocol=pickle.HIGHEST_PROTOCOL)
    if cache is not None and raw in cache:
        return cache[raw]
    b = zlib.compress(raw, 1)
    b = _ROW_BLOBS.setdefault(b, b)
    if cache is not None:
        cache[raw] = b
    return b


def unpack_row(b: bytes) -> dict:
    row = pickle.loads(zlib_ng.decompress(b))
    if "__ix__" not in row:
        return row
    ix, n = row.pop("__ix__"), row.pop("__n__")
    return {k: _whole(v, ix, n, k) for k, v in row.items()}


class _Lazy(Mapping):
    """A stored row (`pack_row`) read field by field: each per-trajectory array is widened to every draw only when
    read (`unpack_row` widens them all at once); nested dicts likewise. The same values, a row's memory at its stored
    trajectories' size (a question pooled from tens of thousands of rows)."""
    __slots__ = ("_c", "_ix", "_n")

    def __init__(self, c: dict, ix, n: int) -> None:
        self._c, self._ix, self._n = c, ix, n

    def __getitem__(self, k):
        v = self._c[k]
        if type(v) is dict:
            return _Lazy(v, self._ix, self._n)
        return _whole(v, self._ix, self._n, k)

    def __iter__(self):
        return iter(self._c)

    def __len__(self) -> int:
        return len(self._c)


def lazy_row(b: bytes):
    row = pickle.loads(zlib_ng.decompress(b))
    if "__ix__" not in row:
        return row
    ix, n = row.pop("__ix__"), row.pop("__n__")
    return _Lazy(row, ix, n)


class Rows:
    """A node's recorded rows, stored compressed; iterating or indexing yields the rows as recorded (LAZY: read field
    by field, `_Lazy`)."""

    __slots__ = ("_b",)
    LAZY: bool = False

    def __init__(self, blobs=()):
        self._b = list(blobs)

    def append(self, row: dict) -> None:
        self._b.append(pack_row(row))

    def append_blob(self, b: bytes) -> None:
        self._b.append(b)

    def blob(self, i: int) -> bytes:
        return self._b[i]

    def copy(self) -> Rows:
        return Rows(self._b)

    def __len__(self) -> int:
        return len(self._b)

    def __iter__(self):
        return ((lazy_row if self.LAZY else unpack_row)(b) for b in self._b)

    def __getitem__(self, i):
        return (lazy_row if self.LAZY else unpack_row)(self._b[i])


@dataclass(frozen=True)
class _Prefix:
    """What the tree builder keeps of one path prefix: per step, the decision day and the path facts code computed at
    it ([draws] each; the traced step's only: later steps read `day[-1]`); the petition day per draw; and a fingerprint of the event cash, encumbrance and credit
    capacity, so two branches merge only where all of them are identical on every trajectory."""
    day: list
    cash: list
    owed: list
    collateral: list
    petition: np.ndarray
    digest: bytes | None  # None: a day-only trace (events.Chain._book_to_day), whose cash to the horizon is unread
    cause: np.ndarray | None = None  # per draw: the rule that booked the earliest petition (events.PETITION_CAUSES)
    marks: dict | None = None  # condition -> the day it holds from, per draw (events.MARKS)
    settle_offer: np.ndarray | None = None  # the traced step's settlement amount on its payment date (0: none)
    stay_offer: np.ndarray | None = None  # cash above the 30-day operating need on the stay-approval day (0: none)
    triggers: dict | None = None  # events.TRIGGERS name -> day index per draw (events.BIG: none)
    raise_offer: np.ndarray | None = None  # the equity available at the cash floor on the decision day (0: none)
    reads: np.ndarray | None = None  # the traced step's latest cash-read day (a payment, approval or levy day)
    sit: dict | None = None  # the traced step's question-state snapshot (events.Chain.c_situation; pending claims)
    groups: np.ndarray | None = None  # the traced step's option group per draw (events.Chain.option_group; -1: not asked)
    as_of: np.ndarray | None = None  # a day-only trace: per draw, the last day whose state it reads
    fired: dict | None = None  # each waiting step's booking day (index -> [draws]; BIG: not booked)
    served: bool = False  # a sibling's trace served for the walk's structure (`Forecaster._served`), not for facts
    light: bool = False  # a walk read's trace (events.Chain.finish light): no triggers, snapshot or fired days
    question: dict | None = None

    @classmethod
    def of(cls, tr, daily: bool = False, whole: bool = False, digest: bool = True, light: bool = False) -> _Prefix:
        """daily: the cash by kind and the incurred days are in the digest too (the processor orders by them); whole:
        every step's facts (the page's path sequence), not only the traced step's; digest: False for a day-only
        trace (its event cash after the decision day is not booked)."""
        from app.analysis.events import KINDS, OBLIGATIONS, array_key

        ev, rows = tr.events, getattr(tr, "rows", None)
        d = None
        if digest:
            extra = [*(ev.kinds[k] for k in KINDS), *(ev.incurred[k] for k in OBLIGATIONS)] if daily else []
            d = array_key(*(() if rows is None else (rows,)), ev.cash, ev.lock, ev.capacity, *extra, ev.petition)
        if rows is None:
            petition = ev.petition.copy()
        else:  # the event cash is on the path's rows: its petition on every draw, 0 off them (`masked`)
            petition = np.zeros(len(tr.day[-1]), dtype=ev.petition.dtype)
            petition[rows] = ev.petition
        if not digest and tr.question_petition is not None:
            petition = tr.question_petition.copy()
        last = slice(None) if whole else slice(-1, None)  # later steps read only the traced step's (`tr.day[-1]`)
        return cls(tr.day[last], tr.cash[last], tr.owed[last], tr.collateral[last], petition, d,
                   None if tr.cause is None else tr.cause.copy(), tr.marks, tr.settle_offer, tr.stay_offer,
                   tr.triggers, getattr(tr, "raise_offer", None), getattr(tr, "reads", None),
                   (getattr(tr, "situations", None) or {}).get(len(tr.day) - 1),
                   (getattr(tr, "groups", None) or {}).get(len(tr.day) - 1), getattr(tr, "as_of", None),
                   getattr(tr, "fired", None), light=light,
                   question=getattr(tr, "questions", {}).get(len(tr.day) - 1))


class _DepthCache:
    """A cache keyed by step prefixes, for a depth-first walk. Per namespace (one chain: a dispute's, the bank's) it
    keeps the prefixes of the current path (the latest request that is not an ancestor of the one before) on a stack (the current path's ancestors, one per
    depth), which nothing evicts; an entry that stops being a prefix of the latest request (a sibling probe, the
    path just left) moves to a small LRU, and returns to the stack when a later request extends it again. An
    ancestor is lost only after `side` other entries have left the stack with no request below it in between.
    Per request: O(d) (d: the depth where the request leaves the previous one's path, the promotions below it)."""

    def __init__(self, side: int) -> None:
        self.side = side
        self.tip: dict = {}  # namespace -> the latest request's steps
        self.levels: dict = {}  # namespace -> {depth: value of tip[:depth]}
        self.lru: dict = {}  # (namespace, steps) -> value, least recently used first

    def _move(self, ns, steps: tuple) -> dict:
        tip = self.tip.get(ns, ())
        lv = self.levels.setdefault(ns, {})
        c, top = 0, min(len(tip), len(steps))
        while c < top and tip[c] == steps[c]:
            c += 1
        if c == len(steps):  # an ancestor of the latest request (a parent's mask, a re-read): the path stays
            return lv
        for L in [L for L in lv if L > c]:  # no longer on the path: to the LRU
            self._side((ns, tip[:L]), lv.pop(L))
        if self.lru:
            for L in range(c + 1, len(steps) + 1):  # back on the path
                v = self.lru.pop((ns, steps[:L]), None)
                if v is not None:
                    lv[L] = v
        self.tip[ns] = steps
        return lv

    def _side(self, key, v) -> None:
        self.lru.pop(key, None)
        if len(self.lru) >= self.side:
            self.lru.pop(next(iter(self.lru)))
        self.lru[key] = v

    def get(self, ns, steps: tuple):
        return self._move(ns, steps).get(len(steps))

    def put(self, ns, steps: tuple, v):
        self._move(ns, steps)[len(steps)] = v
        return v


def masked(p: _Prefix, m: np.ndarray | None) -> _Prefix:
    """The prefix on the path's trajectories only: elsewhere its decision is not taken (day BIG) and a petition
    precedes it (day 0), so a read over trajectories (inside the horizon, before a petition, every one filed, the
    facts `Forecaster.live` keeps) sees the mask's rows alone."""
    if m is None:
        return p
    from app.analysis.events import BIG

    question = None if p.question is None else {**p.question, "day": np.where(m, p.question["day"], BIG),
                                                "petition": np.where(m, p.question["petition"], 0)}
    return replace(p, day=[np.where(m, x, BIG) for x in p.day], petition=np.where(m, p.petition, 0), question=question,
                   groups=None if p.groups is None else np.where(m, p.groups, -1).astype(np.int8))


INTERVAL_PHRASES = {"I1": "before the post-trial ruling", "I2": "after the post-trial ruling, before the appeal deadline",
                    "I3": "judgment enforceable and unstayed, after the appeal deadline",
                    "I4": "judgment stayed on approved security", "Istay": "a stay has taken effect before the ruling",
                    "Iappeal": "the company has filed an appeal", "Ienforce": "the creditor has levied",
                    "post": "after the post-trial ruling",
                    "ripe": "after the post-trial ruling, on the date the notes' judgment default ripens"}
DELISTING_PHRASES = {"delisted_panel": "stock delisted on the Hearings Panel's decision",
                     "delisted_suspension": "stock delisted on suspension, with no hearing"}
STATE_PHRASES = {"entered": "the judgment as entered", "first": "the company's first response to the enforceable judgment",
                 "after_seek": "after the company sought a sale or new financing",
                 "stay_pending": "a stay motion is pending", "levied": "the creditor has levied on the company's cash",
                 "unlevied": "before any levy on the company's cash", "appealed": "the company has appealed",
                 "final": "the appeal period has run and the judgment is final",
                 "motions_pending": "the post-trial motions are pending",
                 "executing": "the creditor sought execution before the post-trial ruling",
                 "stay_moved": "the company has moved for a stay",
                 "stayed": "the judgment is stayed on approved security",
                 "settled": "the company has paid a settlement to the creditor",
                 "paid": "the company has paid the judgment",
                 "seeking": "the company has sought a sale or new financing",
                 "notes_due": "the notes are due and unpaid, and no bankruptcy petition has been filed",
                 "delisted": "the stock has been delisted",
                 "cash_exhausted": "the company did not file when its cash fell below its 30-day operating need",
                 "entered_not_acted": "the holders have not given notice of a default on the judgment as entered",
                 "raised": "the company raised equity when its cash fell below its 30-day operating need"}


def context_phrases(tags: list[str], ranges: dict[str, tuple[int, int]], labels: dict | None = None) -> list[str]:
    """The situation a decision is asked in, in plain terms (the node's context tags rendered for Jev). `labels`: the
    template's label templates filled from case inputs (4.1.0), read before the 4.0.0 phrases."""
    out = []
    for t in tags:
        if labels and t in labels:
            out.append(labels[t])
        elif t in INTERVAL_PHRASES:
            out.append(INTERVAL_PHRASES[t])
        elif t in STATE_PHRASES:
            out.append(STATE_PHRASES[t])
        elif t in ("pay", "nopay", "raise", "noraise"):  # carried by the options offered
            continue
        elif t.startswith("amt"):
            cents = int(t[3:].split("_")[0])
            out.append(f"judgment after the post-trial ruling: {usd(cents)}"
                       + (", with a new trial ordered on the trade-secret damages" if t.endswith("_retrial") else ""))
        elif t in ("none", "retrial"):
            out.append("no money award survives the post-trial ruling"
                       + ("; a new trial is ordered on the trade-secret damages" if t == "retrial" else ""))
        elif t.split("_retrial")[0] in ("beyond", "beyond_up"):
            base, retrial = t.split("_retrial")[0], t.endswith("_retrial")
            lo, hi = ranges.get(t, (None, None))
            span = f"{usd(lo)} to {usd(hi)}" if lo is not None else "an amount beyond the company's cash"
            out.append(f"judgment after the post-trial ruling: {span}"
                       + (", increased by the ruling" if base == "beyond_up" else "")
                       + (", with a new trial ordered on the trade-secret damages" if retrial else ""))
        elif t in DELISTING_PHRASES:
            out.append(DELISTING_PHRASES[t])
        elif t.startswith("delisting_") and t[len("delisting_"):] in DELISTING_PHRASES:
            out.append(f"notes due after the delisting ({DELISTING_PHRASES[t[len('delisting_'):]]})")
        elif t.startswith("judgment_"):
            out.append("notes accelerated on the judgment default")
        else:
            raise ValueError(f"No plain phrase for decision context {t!r}")
    return out


PENDING = "liability_pending"  # a claim at trial (template pending_money_claim, 4.1.0)
NO_JUDGMENT = ("claimed", "no_award", "set_aside")  # a pending claim's amount classes with no money judgment
VERDICT_NODES = ("verdict_finding", "verdict_measure", "verdict_amount")  # the jury's verdict-form questions (4.1.0 verdict_form)
MERITS = ("ts_liability_jmol", "ts_damages_ruling", "remittitur_accepted", "patent_jmol", "trebling", "fees_awarded",
          "prejudgment_interest", "injunction")
# questions about an unpaid judgment: their facts pool only trajectories where an amount is still owed
OWED = {"execute_pre_ruling", "stay_motion", "stay_approved", "registration_early", "debtor_response", "judgment_response",
        "enforce_after_final", "settlement_offer", "settlement_accept", "holders_act_judgment"}
# questions whose actor weighs the 30-day operating need: the cash floor and cash running out
NEED_NODES = ("petition_cash_floor", "financing_at_floor", "petition_cash_out")
# questions whose actor weighs the contract dates ahead: settlement, the cash floor, cash running out, the notes
DATED = {"settlement_offer", "settlement_accept", "petition_cash_floor", "financing_at_floor", "petition_cash_out",
         "holders_act_judgment", "judgment_response", "listing_kept",
         "holders_act_delisting", "holders_involuntary", "petition_on_notes"}
# the conditions the listing and notes questions are asked under, in both the forecast's walk and the ordinary view
ASSUMED = {"listing_kept": ("the stock is not compliant on the deadline",),
           "holders_act_delisting": ("the stock is not listed on an Eligible Market",),
           "petition_on_notes:delisting": ("the holders accelerate the notes",),
           "holders_involuntary:delisting": ("the notes are accelerated and unpaid", "the issuer does not file"),
           "petition_on_notes:repurchase": ("the repurchase falls due unpaid",),
           "holders_involuntary:repurchase": ("the repurchase is unpaid", "the issuer does not file")}
TRIGGER_PHRASES = {
    "judgment_default_entered": "the notes' judgment default (§7.01(i)) on the judgment as entered: 60 days after "
                                "execution became available{since}",
    "judgment_default_ruling": "the notes' judgment default (§7.01(i)) if the judgment is final only on the post-trial "
                               "ruling: 60 days after the court's order on the last pending post-trial motion",
    "appeal_deadline": "the deadline to file a notice of appeal",
    "coupon": "the notes' interest payment date",
    "listing_deadline": "Nasdaq's deadline to regain compliance with the minimum bid price",
    "repurchase_due": "the repurchase date the holders may require after a delisting",
    "holders_petition_earliest": "the earliest date the holders may file a petition (Indenture §7.06)"}
MOTION_PHRASES = {"rule_50b": "renewed motion for judgment as a matter of law (Fed. R. Civ. P. 50(b))",
                  "rule_52b": "motion to amend the findings (Fed. R. Civ. P. 52(b))",
                  "rule_59a": "motion for a new trial or remittitur (Fed. R. Civ. P. 59(a))",
                  "rule_59e": "motion to alter or amend the judgment (Fed. R. Civ. P. 59(e))",
                  "rule_54_fees": "motion for attorneys' fees", "injunction": "motion for a permanent injunction"}
# merits node -> the component kinds its ruling decides (the pending motions that decide them are its path fact)
MERITS_KINDS = {"ts_liability_jmol": (), "ts_damages_ruling": ("compensatory",),
                "remittitur_accepted": ("compensatory",), "patent_jmol": ("patent",), "trebling": ("trebling",),
                "fees_awarded": ("fees",), "prejudgment_interest": ("prejudgment_interest",), "injunction": ()}


def notes_default_text(m: dict, f) -> str:
    """The notes' judgment default in the indenture's own words (contract template quotes), filled from the
    instrument's threshold and days; '' where the instrument states no period."""
    if not f.judgment_default_days:
        return ""
    q = next(t["quotes"]["judgment_default"] for t in m["templates"].values() if "judgment_default" in t.get("quotes", {}))
    return q.format(threshold=usd(f.judgment_default_threshold_cents), days=f.judgment_default_days)


class Forecaster:
    def __init__(self, disputes: list[DisputeInstance], findings: dict[str, AtomicFinding], *, borrower: str,
                 review: date, horizon: date, hydrate: Callable[[AtomicFinding], dict], model: dict | None = None,
                 setup=None, basis=None, sens: dict | None = None, slots: dict[str, dict[str, list[str]]] | None = None) -> None:
        from app.analysis.events import Draws

        self.m = model or load_model()
        self.spec = _q(self.m)
        self.disputes = [d for d in disputes if d.status == "interpreted"]
        self.findings, self.borrower, self.review, self.horizon, self.hydrate = findings, borrower, review, horizon, hydrate
        self.setup, self.sens = setup, sens or {}
        self.slots = slots or {}  # node -> record item -> the accepted findings that supply it (app/disputes/slots.py)
        self.days = (horizon - review).days
        self.draws = Draws(basis.cash.shape[0], basis=basis) if basis is not None else None
        if self.draws is not None:
            self.draws.prefixes = {}  # the tree is walked depth-first: each trace resumes from its prefix
        self.reach = int(basis.cash.max()) if basis is not None else None  # no trajectory holds more cash than this
        self.class_members: dict[str, list[tuple[int, int]]] = {}  # ruling class -> (total, fees) of each outcome
        self.class_range: dict[str, tuple[int, int]] = {}  # merged amount class label -> (min, max) judgment
        self.remit_classes: set[str] = set()  # amount class labels whose outcomes are all remitted and accepted
        self.nodes: dict[str, Node] = {}
        self.facts: dict[str, list] = {}  # node key -> the rows `record` keeps, one per path that asks it
        self._late_seen: set = set()  # (node key, prefix, record digest) kept by `record_late`
        # floor prefixes where equity is unavailable on the prefix but a later-walked step dated before the floor (a
        # set-aside, a payment, a settlement) makes it available on some trajectory: the walk offers the raise there
        self._raise_open: set = set()
        self._raise_more: set = set()
        self.node_group: dict[str, int] = {}  # a grouped question's key -> the option group it is asked of
        # QUESTIONS §1 Grouping, per trajectory: the questions asked in more than one situation class (each class its
        # own question, `class_key`), and per question the classes recorded at each prefix (`_qcls_get`)
        self.classed: set[str] = set()
        self.grouped: set[str] = set()  # questions asked per option group, each draw in its group's class
        self._qcls: dict[str, list] = {}
        # per question, its class per draw as asked (`_Walk.node`: on the branch that books nothing, the state before
        # the decision), at each prefix of the current path: what every branch's paths and rows read
        self._qcanon: dict[str, list] = {}
        self._rec_at: tuple | None = None
        # traced prefixes: the tree is walked depth-first, so the current path's prefixes stay on a stack and only
        # sibling probes share a small LRU (`_DepthCache`); each entry holds its step's question-state snapshot
        self._traces = _DepthCache(self.SIBLINGS)
        self._reuse: dict = {}  # namespace -> depth -> the walked children's traces (`_logged`)
        self.reuse_stats = {"computed": 0, "served": 0}
        self.remitted: dict[str, tuple[int, int, int]] = {}  # C3 node key -> the remitted amount and its band
        self._sources: dict[str, str] = {}  # finding -> its source's title
        self.bank_nodes: dict[str, Node] = {}  # the bank view's questions (bank_state)
        self.bank_facts: dict[str, list] = {}  # bank node key -> [(day, cash, need, raise offer) arrays]
        # the case sets raise_capacity: the company's floor decision is financing_at_floor (4.1.0; retired, 14 May)
        self.raising = "value" in self.m["parameters"].get("raise_capacity", {})
        # QUESTIONS_20240514 §2.6: the case declares a share ledger: the equity channels and the distress loop
        self.equity = "value" in self.m["parameters"].get("share_ledger", {})

    def texts(self, node: str, d: DisputeInstance | None) -> dict:
        """The node's question texts (residual question, actor, decision, branches, standard, record items,
        situation): a pending claim reads them from the node's pending_money_claim block where it has one (contract
        `templates.pending_money_claim.node_texts`), every other dispute from the node."""
        s = self.spec[node]
        if d is not None and d.stage == PENDING and "pending_money_claim" in s:
            return {**s, **s["pending_money_claim"]}
        return s

    def event_forecast(self, n: Node) -> bool:
        """Whether the question is one of the 14 May questions (profile event_forecast, QUESTIONS_20240514)."""
        return registry_entry(n.question_id).get("profile") == "event_forecast"

    def ordered(self) -> list[tuple[DisputeInstance, None]]:
        """4.0.0 models each judgment's components and triggered instruments inside one dispute's chains, so
        disputes compose independently."""
        return [(d, None) for d in sorted(self.disputes, key=lambda d: (d.borrower_role != "debtor", d.instance_id))]

    # --- nodes ------------------------------------------------------------------------------------------------------

    def key(self, d: DisputeInstance, node: str, *ctx: str) -> str:
        return f"{d.instance_id}:{node}" + ("|" + "|".join(ctx) if ctx else "")

    def node(self, d: DisputeInstance, node: str, *ctx: str, assumptions: tuple[str, ...] = (),
             branches: tuple[str, ...] | None = None) -> str:
        k = self.key(d, node, *ctx)
        if k not in self.nodes:
            self.nodes[k] = self.new_node(d, node, *ctx, assumptions=assumptions, branches=branches)
        return k

    def new_node(self, d: DisputeInstance, node: str, *ctx: str, assumptions: tuple[str, ...] = (),
                 branches: tuple[str, ...] | None = None) -> Node:
        """The node `node` creates where its key is new."""
        s = self.texts(node, d)
        return Node(key=self.key(d, node, *ctx), instance_id=d.instance_id, node=node, context="|".join(ctx), cls="",
                    question_id=s["residual_question"], event=s["decision"],
                    assumptions=tuple(assumptions), window=s.get("timing", ""),  # 5.0.0: none
                    branches=tuple(branches or s["branches"]))  # a pending claim: the block's answers

    # --- prefix traces (code timing and arithmetic, before any Jev answer) ----------------------------------------

    def trace(self, d: DisputeInstance, steps: tuple, full: bool = False, real: bool = False,
              light: bool = False, rows: tuple | None = None, full_rows: bool = False) -> _Prefix:
        """The prefix's per-step decision days and path facts, its petition days and a fingerprint of its event cash.
        The dense [draws, days] arrays are dropped once fingerprinted: the tree has thousands of prefixes, and keeping
        each prefix's arrays held about 20 GB for the Akoustis tree. Settled only to the last step's decision day
        (events.Chain._book_to_day: every read as of that day equals the whole path's; no digest) unless `full`: a
        read of the event cash, the petition or marks after the decision day (the digest, a petition anywhere).
        A walk read may be served by a walked sibling's trace (`_served`); `real`: a recorded fact's, never served.
        `light`: a walk read's (`_Walk._raw`), without the facts only a recorded question reads (events.Chain.finish);
        any other read never takes a light trace from the cache. `rows`: per step, the trajectories the path follows
        (`_Walk._rows`): a day-only trace is computed on them alone (events `_run`), off them as `masked` leaves it;
        a full trace (the digest) is always the whole draws'."""
        from app.analysis.events import _rows_key, canon, event_chain, event_trace

        steps = canon(steps)  # a grouped branch books its answer; the path's group is its mask (`_Walk._trace`)
        if (full and not full_rows) or rows is None or not rows or rows[-1] is None or rows[-1].all():
            rows = None  # full_rows: a whole trace on the path's rows (its digest reads those rows only)
        rk = None if rows is None else _rows_key(rows[-1])

        def compute():
            path = DisputePath(instance_id=d.instance_id, steps=steps, outcome="", edges=())
            t = event_trace(d, path, self.setup, self.m, self.draws, self.sens, day_only=not full, light=light,
                            rows=rows)
            return self._prefix(d.instance_id, steps, full, light, t, rk)

        return self._traced(d.instance_id, steps, full, real, compute,
                            lambda st: event_chain(d, st, self.setup, self.m, self.draws, self.sens), light, rk)

    OPEN_LIGHT = 16  # light day-only traces kept completable (each holds its finished chain)

    def _prefix(self, ns, steps: tuple, full: bool, light: bool, t, rk: bytes | None = None) -> _Prefix:
        """The trace `t` as a cached prefix; a light day-only one stays completable (`_traced`) for a while."""
        daily = self.setup.cash_processing == "daily"
        if light and not full:
            open_ = self.__dict__.setdefault("_open_light", {})
            if len(open_) >= self.OPEN_LIGHT:
                open_.pop(next(iter(open_)))
            open_[(ns, steps, rk)] = lambda: (lambda w: None if w is None else _Prefix.of(w, daily, digest=False))(
                t.complete())
        return _Prefix.of(t, daily, digest=full, light=light)

    def _traced(self, ns, steps: tuple, full: bool, real: bool, compute, chain, light: bool = False,
                rk: bytes | None = None) -> _Prefix:
        """A cached prefix trace (`_traces`: per steps, one per row subset `rk`), else a walked sibling's
        (`_served`), else computed; logged for the siblings walked after it (`_logged`). A light trace answers only a
        light read; another read completes it on its finished chain where that is still held
        (events.Chain.completed), else computes it whole."""
        key = (ns, "full") if full else ns
        slot = self._traces.get(key, steps)
        if slot is None:
            slot = self._traces.put(key, steps, {})
        hit = slot.get(rk)
        if hit is not None and real and hit.served:
            hit = None
        if hit is not None and hit.light and not light:
            done = None if full else self.__dict__.get("_open_light", {}).pop((ns, steps, rk), None)
            hit = done() if done is not None else None
            if hit is not None:
                slot[rk] = hit
        if hit is None:
            hit = None if real or not self.REUSE or rk is not None else self._served(ns, steps, full, chain)
            if hit is None:
                hit = compute()
                self.reuse_stats["computed"] += 1
            else:
                self.reuse_stats["served"] += 1
            slot[rk] = hit
        if self.REUSE and not hit.served:
            self._logged(ns, steps, full, hit)
        return hit

    SIBLINGS = 64  # traced prefixes kept off the current path (sibling probes); its ancestors are all kept
    REUSE = False  # exact subtree reuse (`_served`): off; its kept traces cost ~0.75 MB a path and saved no time
    REUSE_CAP = 1500  # traces logged per depth of the current path (memory: each keeps its snapshot)

    # --- exact subtree reuse (Owen's approval, 29 Sep 2026; QUESTIONS §1 Depth applied exactly) ---------------------
    # The walk is depth first and deterministic in what it reads. At each depth of the current path the traces read
    # below each child walked so far are kept (per child step, by the steps after it). A read below a later sibling
    # (the same steps after it) is served by a walked child's trace where it is provably the same: events.Chain
    # .divergence gives, per draw, a day before which the two children's chains book and read the same; the engine
    # is causal, so a trace whose step falls inside the horizon and reads nothing dated on or after that day (its
    # `as_of`), and whose step falls after the horizon only where they agree inside it, is identical on every draw
    # the walk reads. A waiting step's branch books on its own day: that day, in the walked trace, is the divergence.
    # Served traces decide the walk's structure only; every recorded fact is computed (`real`), and every emitted
    # path is traced whole (its key and late facts).

    def _logged(self, ns, steps: tuple, full: bool, v: _Prefix) -> None:
        lv = self._reuse.setdefault(ns, {})
        for j in range(len(steps)):
            e = lv.get(j)
            if e is None or e["s"] != steps[:j]:  # a new parent at this depth: the deeper levels are stale too
                for k in [k for k in lv if k >= j]:
                    del lv[k]
                e = lv[j] = {"s": steps[:j], "kids": {}, "size": 0, "x": {}}
            if e["size"] < self.REUSE_CAP:
                kid = e["kids"].setdefault(steps[j], {})
                if (steps[j + 1:], full) not in kid:
                    kid[(steps[j + 1:], full)] = v
                    e["size"] += 1

    def _served(self, ns, steps: tuple, full: bool, chain) -> _Prefix | None:
        from app.analysis.events import BIG

        lv = self._reuse.get(ns, {})
        for j in range(len(steps) - 1, -1, -1):  # the nearest walked sibling first
            e = lv.get(j)
            if e is None or e["s"] != steps[:j]:
                continue
            for c, kid in e["kids"].items():
                v = kid.get((steps[j + 1:], full))
                if v is None or v.served:
                    continue
                if c == steps[j]:  # the same steps (a group sibling: its branch books the same answer)
                    return replace(v, served=True)
                if j == len(steps) - 1:  # the sibling step itself: its own decision's facts are its own
                    continue
                x = e["x"].get((c, steps[j]))
                if x is None:
                    x = e["x"][(c, steps[j])] = self._divergence(steps[:j], c, steps[j], chain)
                div, wait = x
                if wait:  # the waiting step's own booking day in the walked trace (N1's answer books from the close)
                    f = (v.fired or {}).get(j, BIG)
                    lag = int(self.m["parameters"]["offering_price"]["close_days"]) if steps[j][0] == "offering" else 0
                    div = np.minimum(div, np.where(f < BIG, f + lag, BIG))
                if full:
                    ok = bool((div >= BIG).all())
                else:
                    t = v.day[-1]
                    ok = v.as_of is not None and bool(np.where(t < self.days, v.as_of < div, div >= BIG).all())
                if ok:
                    return replace(v, served=True)
        return None

    def _divergence(self, parent: tuple, c: tuple, b: tuple, chain) -> tuple[np.ndarray, bool]:
        """Per draw, the day before which the chains after `parent + (c,)` and `parent + (b,)` agree (events.Chain
        .divergence), and whether the two steps are one waiting question's branches (their booking day diverges)."""
        ca, cb = chain(parent + (c,)), chain(parent + (b,))
        wait = c[:2] == b[:2] and ca.waits(c[0], c[1]) and any(w[0] == len(parent) for w in ca.waiting) \
            and any(w[0] == len(parent) for w in cb.waiting)
        return ca.divergence(cb, len(parent) if wait else None), wait


    def whole_trace(self, d: DisputeInstance, steps: tuple) -> _Prefix:
        """A whole path's trace with every step's facts (not cached: the page reads each path once)."""
        from app.analysis.events import canon, event_trace

        path = DisputePath(instance_id=d.instance_id, steps=canon(steps), outcome="", edges=())
        return _Prefix.of(event_trace(d, path, self.setup, self.m, self.draws, self.sens),
                          self.setup.cash_processing == "daily", whole=True)

    def situation(self, d: DisputeInstance, steps: tuple, conds: list[str], tr: _Prefix | None = None
                  ) -> tuple[set[str], set[str]]:
        """Of the conditions an actor weighs, those holding at the decision on every trajectory where it is asked, and
        those holding on none. A condition holding on some trajectories only stays unstated. tr: the prefix on the
        path's trajectories (`masked`)."""
        tr = tr if tr is not None else self.trace(d, steps)
        if not conds or tr.marks is None:
            return set(), set(conds)
        t = tr.day[-1]
        pet = np.where(tr.petition < 0, np.iinfo(np.int64).max, tr.petition)
        inside = (t < self.days) & (t < pet)
        if not inside.any():
            return set(), set(conds)
        held = {c: tr.marks[c][inside] <= t[inside] for c in conds}
        return {c for c, h in held.items() if h.all()}, {c for c, h in held.items() if not h.any()}

    def arises(self, d: DisputeInstance, steps: tuple, step: tuple) -> bool:
        """Whether the decision can fall inside the horizon on some trajectory of the path (else its window is
        closed and it is not asked)."""
        tr = self.trace(d, steps + (step,))
        return bool((tr.day[-1] < self.days).any())

    # --- the bank view: the bank data and the common borrower inputs ----------------------------------------------

    def instrument(self):
        """The borrower's instrument whose terms are a common input to both views (the notes' coupon), or None."""
        return next((f for d in self.disputes for f in d.financing if f.status != "superseded"), None)

    def bank_trace(self, steps: tuple, full: bool = False, real: bool = False, light: bool = False) -> _Prefix:
        """The bank (ordinary) view's prefix, settled to its decision day unless `full` (as `trace`)."""
        from app.analysis.events import BANK, bank_trace, canon, event_chain

        steps = canon(steps)

        def compute():
            t = bank_trace(self.instrument(), steps, self.setup, self.m, self.draws, self.sens, day_only=not full,
                           light=light)
            return self._prefix(BANK, steps, full, light, t)

        return self._traced(BANK, steps, full, real, compute,
                            lambda st: event_chain(None, st, self.setup, self.m, self.draws, self.sens,
                                                   fin=self.instrument()), light)

    @property
    def ordinary(self) -> bool:
        """Spec §16.1 (case input ordinary_view): the ordinary view is the same forecast with the event given no cash
        effect, so its questions are the forecast's own questions (`ordinary_state`)."""
        return self.m["parameters"].get("ordinary_view", {}).get("value") == "same_forecast"

    def ordinary_dispute(self) -> DisputeInstance:
        """The event the ordinary view gives no cash effect: its record, parties and standard frame the questions."""
        live = [d for d, _ in self.ordered()]
        if len(live) != 1:
            raise NotImplementedError("the ordinary view gives one event no cash effect; this case has "
                                      f"{len(live)} disputes")
        return live[0]

    def no_cash_effect(self) -> str:
        """The ordinary view's situation, from the model's label template filled from case inputs."""
        text = self.labels(self.ordinary_dispute()).get("no_cash_effect")
        if not text:
            raise ValueError("the dispute model has no no_cash_effect label template for the ordinary view")
        return text

    def bank_paths(self) -> list[DisputePath]:
        """The bank view's paths (none without the operating draws)."""
        return _BankWalk(self).run() if self.draws is not None else []

    async def judge_bank(self, judge: ForecastJudge) -> dict[str, Judgment]:
        async def one(n: Node) -> Judgment:
            if self.ordinary:  # the forecast's own question, on the ordinary view's facts
                if len(tags := ordinary_classes(self, n)) > 1:  # QUESTIONS §1 Grouping: one situation per question
                    raise NotImplementedError(f"the ordinary view's {n.key} pools {len(tags)} situation classes "
                                              f"({sorted(tags)[:3]}); its walk does not split questions by class")
                st, fids, readings = ordinary_state(self, n)
                o = await judge.forecast(n.question_id, st, (n.instance_id, *fids), n.branches)
                return Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                                event=n.event, assumptions=n.assumptions, window=n.window,
                                distribution=answer_distribution(n.key, n.branches, o), confidence=o.confidence,
                                finding_ids=fids, readings=readings, evidence=state_evidence(st),
                                observation_id=o.observation_id,
                                path_facts=st.get("path_facts", st.get("situation")))  # 14 May: the situation
            st = bank_state(self, n)
            o = await judge.forecast(n.question_id, st, (n.instance_id,), n.branches)
            return Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                            event=n.event, assumptions=n.assumptions, window=n.window,
                            distribution=answer_distribution(n.key, n.branches, o), confidence=o.confidence,
                            observation_id=o.observation_id, path_facts=st.get("path_facts", st.get("situation")))

        results = await asyncio.gather(*(one(n) for n in self.bank_nodes.values()))
        return {j.key: j for j in results}

    def moves_cash(self, d: DisputeInstance, steps: tuple, a: tuple, b: tuple) -> bool:
        """Whether two branches of a step book different event cash, encumbrance, credit capacity or petition day on
        some trajectory (else they merge). Light traces: the digest reads only the event cash, which a light trace
        keeps whole (shadow `light_trace`); the before-answer question captures and court rereads are not needed."""
        return (self.trace(d, steps + (a,), full=True, light=True).digest
                != self.trace(d, steps + (b,), full=True, light=True).digest)

    def pay_possible(self, d: DisputeInstance, steps: tuple, step: tuple) -> bool:
        """Arithmetic: 'pay' stays unless the amount owed exceeds available cash on every trajectory of the path at
        the decision date."""
        tr = self.trace(d, steps + (step,))
        inside = tr.day[-1] < self.days
        return bool((inside & (tr.cash[-1] >= tr.owed[-1]) & (tr.owed[-1] > 0)).any())

    # --- the post-trial ruling: merits nodes and the chain rule --------------------------------------------------

    def merits(self, d: DisputeInstance) -> dict[str, str]:
        """Which merits nodes the record's pending motions put before the court (node -> key)."""
        kinds = {c.kind: c for c in d.components}
        decided = {x for mo in d.motions for x in mo.decides}
        by_kind = {mo.kind for mo in d.motions}
        out = {}
        comp = kinds.get("compensatory")
        if "liability" in decided or "rule_50b" in by_kind:
            out["ts_liability_jmol"] = ()
        if comp is not None and comp.motion:
            out["ts_damages_ruling"] = ("liability survives",)
            out["remittitur_accepted"] = ("the court remits the damages",)
        for kind, node in (("patent", "patent_jmol"), ("trebling", "trebling"), ("fees", "fees_awarded"),
                           ("prejudgment_interest", "prejudgment_interest")):
            c = kinds.get(kind)
            asked = c is not None and (c.motion or (kind == "patent" and "rule_50b" in by_kind)
                                       or (kind == "prejudgment_interest" and d.commenced is not None))
            if asked:
                out[node] = () if kind == "patent" else ("trade-secret money survives the ruling",)
        if "injunction" in by_kind and not self.spec["injunction"].get("stress_only"):
            out["injunction"] = ()
        return {n: self.node(d, n, assumptions=a) for n, a in out.items()}

    def ruling_classes(self, d: DisputeInstance) -> dict[str, list[list[tuple[str, str]]]]:
        """Each ruling outcome's conjunction of merits answers, grouped into amount classes by arithmetic: 'none',
        each amount some trajectory could fund, and 'beyond' / 'beyond_up' (collateral at its lower bound exceeds the
        most cash any trajectory holds, so pay, a self-funded bond, the levy and the settlement bound are identical;
        an increase over the entered judgment is its own class because its second writ, on the increase once enforceable, can move cash).
        tests/test_chains.py checks every merged class is cash- and date-identical; Jev is told the class's range."""
        from app.analysis.events import ruling_amounts

        k = self.merits(d)
        leaves: list[tuple[list, dict]] = [([], {})]

        def split(node, field_, branches, when=lambda o: True):
            nonlocal leaves
            if node not in k:
                return
            nxt = []
            for atoms_, o in leaves:
                if not when(o):
                    nxt.append((atoms_, o))
                    continue
                nxt += [(atoms_ + [(k[node], b)], {**o, field_: b}) for b in branches]
            leaves = nxt

        survives = lambda o: o.get("liability") != "granted"  # noqa: E731
        split("ts_liability_jmol", "liability", ("granted", "denied"))
        split("ts_damages_ruling", "damages", ("stands", "remit", "new_trial"), survives)
        split("remittitur_accepted", "remittitur", ("accept", "new_trial"), lambda o: o.get("damages") == "remit")
        split("patent_jmol", "patent", ("granted", "denied"))
        money = lambda o: survives(o) and (o.get("damages", "stands") == "stands"  # noqa: E731
                                           or o.get("remittitur") == "accept")
        for node, f in (("trebling", "trebling"), ("fees_awarded", "fees"), ("prejudgment_interest", "interest")):
            split(node, f, ("granted", "denied"), money)
        from app.analysis.events import entered_cents

        lower = self.m["parameters"]["bond_collateral_share_bps"]["lower"] / 10_000
        entered = entered_cents(d)
        classes: dict[str, list] = {}
        members: dict[str, list[tuple[int, int]]] = {}
        remitted: dict = {}  # class -> whether each of its outcomes is a remitted amount the creditor accepted
        for atoms_, o in leaves:
            a = ruling_amounts(d, o, self.m)
            total = sum(a.values())
            retrial = "new_trial" in (o.get("damages"), o.get("remittitur"))  # the dispute goes on (legal spend too)
            if total == 0:
                c = "none"
            elif self.reach is not None and total * lower > self.reach:
                # beyond reach; an increase keeps its own class (its second writ moves cash)
                c = "beyond_up" if total > entered else "beyond"
            else:
                c = f"amt:{total}:{a['fees']}"
            c = (c, retrial)
            classes.setdefault(c, []).append(atoms_)
            members.setdefault(c, []).append((total, a["fees"]))
            remitted.setdefault(c, []).append(o.get("remittitur") == "accept")
        out = {}
        for (c0, retrial), parts in classes.items():
            c = (c0, retrial)
            lo = min(members[c])
            label = f"{c0}:{lo[0]}:{lo[1]}" if c0.startswith("beyond") else c0
            label = ("retrial" if label == "none" else f"{label}:retrial") if retrial else label
            out[label] = parts
            self.class_members[label] = members[c]
            if all(remitted[c]):
                self.remit_classes.add(_label(label))
            if c0.startswith("beyond"):  # what Jev is told: the class's range, never one figure
                self.class_range[_label(label)] = (lo[0], max(members[c])[0])
        return out

    def verdict_lines(self, d: DisputeInstance, inflows: np.ndarray | None = None) -> dict:
        """J1b's cut points on the total judgment (QUESTIONS §4.1 J1b), measured on the built trajectories before
        the tree is built. `inflows`: [draws, days] net equity proceeds received by each day (cumulative), at the most
        the equity channels can deliver (worker A's at-the-market sales and offerings; None: no equity inflow).
        - reach: the most Akoustis can pay from cash on the entry day (the most entry-day cash on any trajectory);
        - top: the lowest amount above which every award books the same cash on every trajectory through the
          horizon: an amount owed that no day's balance (the balance a levy attaches, end-of-day cash, the cash above
          the month's need over the bond collateral share) can reach, and above the notes' judgment-default threshold;
        - cuts: nothing, one further cut in each band below the top line (its midpoint), the reach and top lines, and
          the notes' threshold wherever a band would straddle it.
        Returns the lines and the bands, each band with the amount it books (the top band: the claimant's amount)."""
        from app.analysis.events import BIG, Chain, Trace, pending_template, verdict_amount

        key = (d.instance_id, None if inflows is None else hash(np.asarray(inflows).tobytes()))
        memo = self.__dict__.setdefault("_lines", {})
        if key in memo:
            return memo[key]
        ch = Chain(d, self.setup, self.m, self.draws, self.sens)
        ch.instrument_cash()
        branches = pending_template(self.m)["verdict_branches"]
        claimant = next(b for b, v in branches.items() if v.get("adverse"))
        ch.advance(Trace(ch.ev), "verdict", "I0", claimant)  # books nothing but the entry day
        cum, E, n, N = ch.cum(), ch.E_ix, ch.n, ch.N
        eq = np.zeros((n, N), dtype=np.int64) if inflows is None else np.asarray(inflows, dtype=np.int64)
        after = (np.arange(N)[None, :] >= E[:, None]) & (E < N)[:, None]
        prev = np.concatenate([np.full((n, 1), ch.basis.opening, dtype=cum.dtype), cum[:, :-1]], axis=1)
        balance = prev + ch.basis.inflow + eq  # the balance a levy served that day attaches (§2.2)
        end = cum + eq
        reach = int(end[ch.rows, np.minimum(E, N - 1)].max())
        years = ch.p("bond_forward_interest_years")
        share = (self.setup.collateral_share[0] if self.setup.collateral_share else
                 self.m["parameters"]["bond_collateral_share_bps"]["lower" if self.sens.get("bond_collateral_share_bps")
                                                                  else "value"] / 10_000)
        covers = (end - ch.basis.need) / (share * (1 + ch.bps / 10_000 * years))  # full collateral within reach
        most = max(int(np.where(after, balance, 0).max()), int(np.where(after, end, 0).max()),
                   int(np.ceil(np.where(after, covers, 0).max())))
        f = ch.fin
        thr = (f.judgment_default_threshold_cents + f.insured_cents) if f is not None and \
            f.judgment_default_threshold_cents else 0
        up = lambda x: -(-int(x) // 100) * 100  # noqa: E731  whole dollars, rounded up (a line is never below its measure)
        reach, top = up(reach), up(max(most, thr))
        cuts = sorted({0, up(reach // 2), reach, up((reach + top) // 2), top})
        if thr and thr not in cuts and any(a < thr < b for a, b in zip(cuts, cuts[1:], strict=False)):
            cuts = sorted({*cuts, thr})
        top_amount = verdict_amount(d, self.m, claimant, self.sens)
        if top_amount <= top:
            raise ValueError(f"the claimant's amount {top_amount} is not above the J1b top line {top}")
        bands = [(0, 0, 0)] + [(a, b, (a + b) // 2) for a, b in zip(cuts, cuts[1:], strict=False)] \
            + [(top, BIG * 10**6, top_amount)]
        out = {"reach": reach, "top": top, "threshold": thr, "cuts": cuts, "bands": bands, "top_amount": top_amount}
        memo[key] = out
        return out

    def reduced_band(self, d: DisputeInstance, entered: int) -> tuple[int, int, int] | None:
        """J2's surviving band and booked midpoint, below the original award's J1b band."""
        if entered <= 0:
            return None
        bands = [b for b in self.verdict_lines(d, self.equity_inflows(d))["bands"] if b[1] > 0]
        at = next(i for i, (lo, hi, _) in enumerate(bands) if lo < entered <= hi)
        return bands[at - 1] if at > 0 else None

    def equity_inflows(self, d: DisputeInstance) -> np.ndarray | None:
        """The integration point for the J1b top line: [draws, days] cumulative net equity proceeds the channels can
        deliver by each day at their most (QUESTIONS §2.6: at-the-market sales from the review date and offerings
        up to the share capacity), or None where the case has no share ledger (4.0.0).
        The at-the-market sales are not in it: `instrument_cash` books them into the Chain's own cash, at the full
        pace from the review date on the verdict line's path (no petition, no delisting), so `verdict_lines` reads
        them from `cum`. The offerings are the company's decision (D7, D8), so no path is assumed to book them; at
        their most they run back to back from the review date (the earliest any occasion can initiate one), each
        initiated the day the last closes, on the Chain's own terms and ledger (January's gross, or the shares left
        times the price), until no capacity is left or the next would close after the horizon. This bounds every
        path's offering proceeds by each day: a later occasion only finds fewer shares left and closes later."""
        from app.analysis.events import BIG, Chain

        if "value" not in self.m["parameters"].get("share_ledger", {}):
            return None
        memo = self.__dict__.setdefault("_inflows", {})
        if d.instance_id in memo:
            return memo[d.instance_id]
        ch = Chain(d, self.setup, self.m, self.draws, self.sens)
        ch.instrument_cash()
        day, k = np.zeros(ch.n, dtype=np.int64), 0
        while True:
            occasion = f"top_line_{k}"
            if not ch.initiate(day, occasion).any():
                break
            ch.offering_outcome(occasion, True)
            day = np.where(ch.offerings[-1][2], ch.offerings[-1][1], BIG)
            k += 1
        per = np.zeros((ch.n, ch.N), dtype=np.int64)
        for o in ch._offers:
            rows = np.flatnonzero(o["closed"])
            per[rows, o["close"][rows]] += o["net"][rows]
        out = np.cumsum(per, axis=1)
        memo[d.instance_id] = out
        return out

    def verdict_classes(self, d: DisputeInstance) -> dict[str, list[list[tuple[str, str]]]]:
        """The jury's verdict, in the form's order (case verdict_form; QUESTIONS §4.1 J1, J1b), as disjoint
        conjunctions of its answers per booked award (exhaustive over the answers). J1 asks the liability items;
        J1b asks, for each amount item (1(b), 1(d), 2(c)), whether it exceeds each J1b line above the total already
        established (`verdict_lines`; the first cut at nothing), in ascending order, each conditional on the band
        established. The walk branches on the total judgment, the sum of every amount entered (Owen's ruling, 29 Sep
        2026: it is all later steps read). With no trade-secret or conspiracy amount the total is exact from the items
        found, 3(a) and 5(a) at the amounts claimed (none where small_claims_awarded is false): one class each. With
        one entered, the class is the total's J1b band, booked at its midpoint (the top band: the claimant's amount);
        the small items move only the band, never the booking inside it. Nothing is asked once the total is above the top line, and exemplary damages
        (1(c), 1(d), at most twice 1(b)) only where they can move the total across a line. Each class is labelled
        'no_award' or 'award:<booked>:<band low>:<band high|top>' (an exact class: low = high = booked); `verdict_asks` holds each amount question's item,
        threshold and the total established before it."""
        from app.analysis.events import pval

        form, lines = self.m["case_verdict_form"], self.verdict_lines(d, self.equity_inflows(d))
        cuts, top = lines["cuts"], lines["top"]
        comp = {c.component_id: c.amount_cents for c in d.components}
        small = (pval(self.m, "small_claims_awarded", self.sens.get("small_claims_awarded", False))
                 if "small_claims_awarded" in self.m["parameters"] else True)
        TOP = -1  # the total is above the top line
        out: dict[str, list] = {}
        self.verdict_asks = getattr(self, "verdict_asks", {})

        def band(x: int) -> tuple[int, str]:
            if x <= 0:
                return 0, "0"
            return next((a, str(b)) for a, b in zip(cuts, cuts[1:], strict=False) if a < x <= b)

        def finish(conj: list, amt: int, fixed: int) -> None:
            if amt == TOP:
                label = f"award:{lines['top_amount']}:{top}:top"
            elif amt + fixed > top:  # above the top line every award books the same cash: the top class
                label = f"award:{lines['top_amount']}:{top}:top"
            elif amt == 0:  # no trade-secret or conspiracy amount: the total is exact from the items found
                label = "no_award" if fixed == 0 else f"award:{fixed}:{fixed}:{fixed}"
            else:  # an amount entered: the total judgment's band, booked at its midpoint (the ends: the sensitivity)
                lo, hi = band(amt + fixed)
                label = f"award:{(lo + int(hi)) // 2}:{lo}:{hi}"
            out.setdefault(label, []).append(conj)

        def walk(q: str, trail: tuple, conj: list, amt: int, fixed: int, hi: dict) -> None:
            if q == "end" or amt == TOP:
                return finish(conj, amt, fixed)
            spec = form["questions"][q]
            if spec["node"] == "verdict_finding":
                if "exemplary_of" in spec:  # only where exemplary damages can move the total across a line
                    cap = spec.get("times", 2) * hi.get(spec["exemplary_of"], 0)
                    if not any(amt < c < amt + cap for c in cuts):
                        return walk(spec["no"], trail, conj, amt, fixed, hi)
                k = self.node(d, "verdict_finding", q, *trail)
                for ans in ("yes", "no"):
                    add = int(comp.get(spec.get("award")) or 0) if ans == "yes" and small else 0
                    walk(spec[ans], trail + (f"{q}={ans}",), conj + [(k, ans)], amt, fixed + add, hi)
                return
            nxt = spec["next"]
            limit = amt + spec["times"] * hi[spec["of"]] if "of" in spec else None
            above = [c for c in cuts if c > amt or c == amt == 0]
            above = [c for c in above if limit is None or c < limit]
            if not above:
                return walk(nxt, trail, conj, amt, fixed, hi)
            t, cj = trail, conj
            for i, c in enumerate(above):
                x = c - amt
                k = self.node(d, "verdict_amount", q, f"X={x}", *t)
                self.verdict_asks[k] = {"item": q, "threshold": x, "established": amt, "line": c}
                if i == 0:  # at most the next line: nothing (the first cut) or the total stays in its band
                    walk(spec.get("zero", nxt) if c == amt == 0 else nxt, t + (f"{q}>{x}=no",), cj + [(k, "no")],
                         amt, fixed, {**hi, q: x})
                else:
                    walk(nxt, t + (f"{q}>{x}=no",), cj + [(k, "no")], (above[i - 1] + c) // 2, fixed, {**hi, q: x})
                t, cj = t + (f"{q}>{x}=yes",), cj + [(k, "yes")]
            last = above[-1]
            if last == top:
                return walk(nxt, t, cj, TOP, fixed, hi)
            nb = next(c for c in cuts if c > last)  # capped below the next line: the band above the last line
            walk(nxt, t, cj, (last + nb) // 2, fixed, {**hi, q: limit - amt})

        walk(form["start"], (), [], 0, 0, {})
        return out

    def labels(self, d: DisputeInstance) -> dict[str, str]:
        """A pending claim's label templates filled from case inputs (never a party name in the contract); {} for 4.0.0
        disputes. The claimant's branch is told its range over the enhancement settings, never one figure."""
        if d.stage != PENDING:
            return {}
        from app.analysis.events import settlement_terms, verdict_amount, verdict_basis

        lo, hi = (verdict_amount(d, self.m, "claimant_theory", {"claimant_enhancements": x}) for x in (False, True))
        lower, how = verdict_basis(d, self.m, "without_principal_measure", self.sens)
        fill = {"claimant": d.counterparty, "range": f"{usd(min(lo, hi))} to {usd(max(lo, hi))}",
                "amount": usd(lower) + (" (the case's declared bound; the record leaves a component's amount unknown)"
                                        if how == "bound" else ""),
                **self.m.get("case_labels", {})}
        mode, count = settlement_terms(self.m, self.sens)
        fill["installments"] = count
        out = {k: v.format_map(fill) for k, v in self.m["templates"]["pending_money_claim"]["label_templates"].items()}
        installments = out.pop("settled_installments", None)
        if mode == "installments" and installments:  # the situation after an agreed settlement, on the case's terms
            out["settled"] = installments
        return out

    def verdict_context(self, n: Node) -> dict:
        """A verdict-form question as the jury meets it: the question quoted, what is asked of it, and its earlier
        answers on the form; an amount question (J1b) also its threshold, as a figure."""
        form = self.m["case_verdict_form"]
        tags = [c for c in n.context.split("|") if c]
        q = form["questions"][tags[0]]
        earlier = []
        for x in tags[1:]:
            if x.startswith("X="):
                continue
            e, ans = x.rsplit("=", 1)
            if ">" in e:  # an earlier amount answer: above or at most a figure
                item, fig = e.split(">")
                said = ("more than " + usd(int(fig)) if ans == "yes"
                        else "no amount" if fig == "0" else usd(int(fig)) + " or less")
                earlier.append(f"{form['questions'][item]['form']}: {said}")
            else:
                earlier.append(f"{form['questions'][e]['form']}: {'Yes' if ans == 'yes' else 'No'}")
        out = {"verdict_form": form["source"], "form_question": f"{q['form']}: \u201c{q['quote']}\u201d",
               **({"asked": q["asks"]} if "asks" in q else {}), "earlier_answers": earlier}
        ask = getattr(self, "verdict_asks", {}).get(n.key)
        if ask is not None:
            out["threshold"] = usd(ask["threshold"])
        return out


    # --- the chain walk ---------------------------------------------------------------------------------------------

    def paths(self, d: DisputeInstance) -> list[DisputePath]:
        """Every structurally feasible path through the dispute's chains (see the module docstring)."""
        if d.borrower_role != "debtor" or d.stage not in ("post_trial", "judgment_entered", "enforcement",
                                                           "appeal_filed", "appeal_pending", PENDING):
            return [DisputePath(instance_id=d.instance_id, steps=(), outcome="outside_chains", edges=())]
        W = _Walk(self, d)
        if not W.pend:
            return W.run()
        while True:  # a pending claim: walk again where a whole path shows equity the floor's prefix did not
            kept = (dict(self.nodes), {k: v.copy() for k, v in self.facts.items()}, set(self._late_seen),
                    set(self.classed), {k: list(v) for k, v in self._qcls.items()},
                    {k: list(v) for k, v in self._qcanon.items()})
            self._raise_more = set()
            out = W.run()
            more = self._raise_more - self._raise_open
            if not more:  # QUESTIONS §1 Depth: financially equivalent paths are one path
                return merge_equivalent(out, W.keys, {k: n.branches for k, n in self.nodes.items()})
            self._raise_open |= more
            self.nodes, self.facts, self._late_seen, self.classed, self._qcls, self._qcanon = kept
            W = _Walk(self, d)

    def all_paths(self) -> dict[str, dict[str, list[DisputePath]]]:
        return {d.instance_id: {"": self.paths(d)} for d, _ in self.ordered()}

    CLASSES = __import__("os").environ.get("SLOPE_CLASSES") != "0"  # 0: every question pools its situations

    def _classified(self, k: str) -> bool:
        """Whether question k is asked per situation class: a 14 May question with cash facts."""
        memo = self.__dict__.setdefault("_cq", {})
        if k in self.grouped:
            return True
        if k not in memo:
            n = self.nodes.get(k)
            d = None if n is None else next((x for x in self.disputes if x.instance_id == n.instance_id), None)
            memo[k] = bool(self.CLASSES and d is not None and d.stage == PENDING and CLASS_TAG not in k
                           and self.event_forecast(n) and n.question_id not in self.no_cash)
        return memo[k]

    def class_key(self, k: str, tag: str) -> str:
        """Question k in situation class `tag`: its own question (created through `node`, as the walk creates one)."""
        from app.analysis.events import group_branches

        n = self.nodes[k]
        d = next(x for x in self.disputes if x.instance_id == n.instance_id)
        self.classed.add(k)
        branches = n.branches
        if k in self.grouped:  # the group's own answers (Owen's ruling, 29 Sep 2026: each class is one question with
            code = int(tag.rsplit(".g", 1)[1])  # the group's own answers), whatever prefix first created the question
            branches = group_branches(n.node, code)
        key = self.node(d, n.node, *[c for c in n.context.split("|") if c], tag, assumptions=n.assumptions,
                        branches=branches)
        if k in self.grouped:
            self.node_group[key] = code
        return key

    def uses_appeal_status(self, n: Node) -> bool:
        """Whether this question consumes the deadline status as well as a filed appeal."""
        d = next((d for d in self.disputes if d.instance_id == n.instance_id), None)
        fields = self.texts(n.node, d).get("situation_keys", ())
        return bool(set(fields) & {"judgment_status", "appeal_deadline"}) or bool(
            set(n.context.split("|")) & {"final", "appealed"})

    def uses_stay_status(self, n: Node) -> bool:
        """Current stay status is consumed by the judgment-status wording."""
        d = next((d for d in self.disputes if d.instance_id == n.instance_id), None)
        return "judgment_status" in self.texts(n.node, d).get("situation_keys", ())

    def question_class(self, n: Node, row: dict, live: np.ndarray | None = None) -> np.ndarray | None:
        """Partition dated statuses when the question renders or uses them."""
        return situation_class(row, live, appeal=self.uses_appeal_status(n), stay=self.uses_stay_status(n))

    def dated_class(self, d, key: str, steps: tuple, row: dict):
        """Class and historical context at a completed before-decision snapshot."""
        node = self.nodes[key]
        live = self.live(node, row)
        cls = self.question_class(node, row, live)
        walk = _Walk(self, d)
        conds = walk.situation_conditions(node.node)
        if cls is None or not conds:
            return cls
        verdict = next((s[2] for s in steps if s[0] == 'verdict'), '')
        ruling = next((s[2] for s in steps if s[0] == 'post_trial_ruling'), '')
        label = 'award' + verdict.split(':')[1] if verdict.startswith('award:') else verdict or 'claimed'
        after = ('reduced' + ruling.split(':')[1] if ruling.startswith('reduced:') else
                 'set_aside' if ruling == 'set_aside' else label)
        ruled = row['marks']['ruled'] <= row['day']
        for selected, value in ((live & ~ruled, label), (live & ruled, after)):
            if selected.any():
                part = np.where(selected, cls, '')
                tagged = walk.context_class(part, row, _S(cls=value), node.node, (node.context.split('|')[0],))
                cls[selected] = tagged[selected]
        return cls

    def deferred_decision(self, key: str) -> bool:
        """Question boundaries resolved by the engine after earlier-dated events are known."""
        node = self.nodes[key]
        d = next((d for d in self.disputes if d.instance_id == node.instance_id), None)
        if d is None or d.stage != PENDING:
            return False
        return node.node in ('stay_approved', 'registration_early', 'financing_at_floor', 'petition_cash_out', 'offering_closes',
                             'petition_on_notes', 'holders_involuntary', 'bid_compliance', 'hearing_request',
                             'holders_act_delisting') or (
            node.node == 'judgment_response' and node.context.split('|')[0] in ('ripe', 'post')) or (
            node.node in ('settlement_offer', 'settlement_accept') and node.context.split('|')[0] == 'I3')

    def completed_probe(self, key: str, steps: tuple, index: int) -> tuple | None:
        """Neutral own action, retaining other dated events for completed decision snapshots.

        Registration is decided on the order day, not the motion day. A ruling or
        settlement traversed later may already have changed the judgment by then.
        """
        name = self.nodes[key].node
        if name == 'registration_early':
            phase = self.nodes[key].context.split('|')[0]
            node, context, _ = steps[index]
            neutral = (node, context, 'no' if node == 'registration_early' else 'none')
            return steps[:index] + (neutral,) + steps[index + 1:] + (
                ('court_order', 'registration_' + phase, ''),)
        if name not in ('bid_compliance', 'hearing_request', 'holders_act_delisting'):
            return None
        node, context, _ = steps[index]
        if name == 'holders_act_delisting':
            if node != 'delisting_notes':
                raise ValueError(f'{name} has no delisting origin at {index}')
            neutral = probe = (node, context, 'none')
        else:
            if node != 'listing':
                raise ValueError(f'{name} has no listing origin at {index}')
            neutral = (node, context, 'compliant')
            probe = ('listing_date', 'compliance' if name == 'bid_compliance' else 'hearing_request', '')
        return steps[:index] + (neutral,) + steps[index + 1:] + (probe,)

    def _split(self, keys, row: dict, keep, cls: np.ndarray | None = None) -> np.ndarray | None:
        """The row kept (`keep(key, row)`) under each class of its questions, on that class's trajectories: the
        classes the questions were asked in (`cls`), else the row's own; returns them (None: not classed, the row is
        kept whole)."""
        from app.analysis.events import BIG

        classified = []
        for key in keys:
            if self._classified(key):
                classified.append(key)
            else:
                self.classed.discard(key)
                keep(key, row)
        keys = tuple(classified)
        if not keys:
            return None
        if cls is not None and "note_context" in row:
            cls = cls.astype(object)
            for text in set(row["note_context"][cls != ""]):
                on = (cls != "") & (row["note_context"] == text)
                cls[on] = np.strings.add(cls[on].astype(str), ".ctx" + str(text).encode().hex()).astype(object)
        if cls is None:
            cls = self.question_class(self.nodes[keys[0]], row, self.live(self.nodes[keys[0]], row))
        if cls is None:
            for k in keys:
                keep(k, row)
            return None
        # Use the shared before-answer class, including when this row records the
        # appeal's yes answer. The answer must not condition its own probability.
        from app.disputes.state14 import appeal_state

        status = appeal_state(row).copy()
        for tag in {t for t in cls if t}:
            part = next((p for p in tag.split(".") if len(p) == 7 and p.startswith("appeal")
                         and p[-1] in "012345"), None)
            if part is not None:
                status[cls == tag] = int(part[6:])
        row = {**row, "appeal_state": status}
        if self.nodes[keys[0]].node == "offering_closes":
            # A previously traversed answer may occur later in calendar time.
            failed = np.zeros(len(row["day"]), dtype=bool)
            for init, close, closed in (row.get("sit") or {}).get("offerings", ()):
                failed |= (init < row["day"]) & (close <= row["day"]) & ~np.asarray(closed, dtype=bool)
            cls = cls.copy()
            for i in np.flatnonzero(failed & (cls != "")):
                cls[i] += ".after_failed"
        for t in sorted({x for x in cls if x}):
            part = {**row, "day": np.where(cls == t, row["day"], BIG)}
            for k in keys:
                keep(self.class_key(k, t), part)
        return cls

    def _qcls_put(self, k: str, at: tuple | None, cls: np.ndarray) -> None:
        """Question k's classes recorded at the prefix `at` (its trace's steps); entries off the current path (a
        finished sibling) are dropped."""
        lst = self._qcls.setdefault(k, [])
        if at is not None:
            lst[:] = [e for e in lst if e[0] is not None and at[:len(e[0]) - 1] == e[0][:-1]]
        lst.append((at, cls))

    def canon_put(self, k: str, prefix: tuple, cls: np.ndarray) -> None:
        """Question k's classes as asked at `prefix`; entries off the current path are dropped."""
        lst = self._qcanon.setdefault(k, [])
        lst[:] = [e for e in lst if prefix[:len(e[0])] == e[0] and e[0] != prefix]
        lst.append((prefix, cls))

    def canon_get(self, k: str, steps: tuple) -> np.ndarray | None:
        """Question k's classes as asked on the path `steps`: the entry at its longest prefix."""
        best = None
        for prefix, cls in self._qcanon.get(k, ()):
            if steps[:len(prefix)] == prefix and (best is None or len(prefix) >= len(best[0])):
                best = (prefix, cls)
        return None if best is None else best[1]

    def row_of(self, tr) -> dict:
        """A question's recorded facts from its prefix trace (`record`)."""
        if getattr(tr, "question", None) is not None:
            return tr.question
        return {"day": tr.day[-1], "cash": tr.cash[-1], "owed": tr.owed[-1], "collateral": tr.collateral[-1],
                "petition": tr.petition, "settle_offer": getattr(tr, "settle_offer", None),
                "stay_offer": getattr(tr, "stay_offer", None), "triggers": getattr(tr, "triggers", None),
                "raise_offer": getattr(tr, "raise_offer", None), "sit": getattr(tr, "sit", None),
                "marks": getattr(tr, "marks", None), "groups": getattr(tr, "groups", None)}

    def _qcls_get(self, k: str, steps: tuple) -> np.ndarray | None:
        """Question k's classes as recorded on the path `steps`: the record whose prefix it shares furthest."""
        return qcls_best(self._qcls.get(k, ()), steps)

    def record(self, keys, tr) -> list:
        """Keep, for each node the step asks, the facts code computed at the decision on every trajectory: its day,
        cash, amount owed and bond collateral, the petition day, and (where the chain computes them) the settlement
        offer, the reduced-security proposal and the dated contract triggers."""
        row = self.row_of(tr)
        out = []

        def keep(k, r):
            b = pack_row(r)
            self.facts.setdefault(k, Rows()).append_blob(b)
            out.append((k, b))
        # as of the decision day (a later-walked step dated after it has not happened), per situation class
        classified = tuple(k for k in keys if self._classified(k))
        asked = (self.canon_get(classified[0], self._rec_at)
                 if classified and self._rec_at is not None and not self.deferred_decision(classified[0]) else None)
        cls = self._split(keys, as_of(row), keep, asked)
        if cls is not None and asked is None:
            quiet = self._rec_at and self._rec_at[-1][2].split("=")[-1] in ("", "no", "none", "neither")
            for k in classified:
                self._qcls_put(k, self._rec_at, cls)
                if quiet and not self.deferred_decision(k):  # deferred canon stores enumeration domains only
                    self.canon_put(k, self._rec_at[:-1], cls)
        return out

    def record_late(self, d: DisputeInstance, steps: tuple, late: tuple, mask: np.ndarray | None = None,
                    tr=None, keep=None) -> dict:
        """Read dated decision facts on the supplied history and support.

        The structural walker supplies its completed path to resolve earlier-dated
        events traversed out of order. The chronological walker instead supplies
        the reached prefix and a `keep` callback to retain the record at its date;
        it never calls this reconstruction to rewrite frozen records at finishing.
        """
        from app.analysis.events import BIG, event_trace

        def on(row: dict) -> dict:
            if mask is None:
                return row
            return {**row, "day": np.where(mask, row["day"], BIG),
                    **({"petition": np.where(mask, row["petition"], 0)} if row.get("petition") is not None else {})}

        if tr is None:
            tr = event_trace(d, DisputePath(instance_id=d.instance_id, steps=steps, outcome="", edges=()), self.setup,
                             self.m, self.draws, self.sens,
                             rows=None if mask is None else tuple(mask for _ in steps))
        store = self._keep_late if keep is None else keep
        classes: dict = {}
        for k, i in late:
            completed = self.completed_probe(k, steps, i)
            if completed is not None:
                from app.analysis.events import event_questions

                cache = self.__dict__.setdefault('_dated_probe_rows', {})
                identity = (d.instance_id, completed, pack_mask(mask))
                row = cache.get(identity)
                if row is None:
                    questions, _ = event_questions(d, DisputePath(d.instance_id, completed, '', ()), self.setup,
                                                  self.m, self.draws, self.sens, indices=(-1,), day_only=True,
                                                  rows=None if mask is None else tuple(mask for _ in completed))
                    row = as_of(on(questions[-1]))
                    if self.SIBLINGS > 0:
                        if len(cache) >= self.SIBLINGS:
                            cache.pop(next(iter(cache)))
                        cache[identity] = row
                got = self._split((k,), row, lambda key, r, i=i: store(key, steps[:i], r),
                                  self.dated_class(d, k, steps, row))
                if got is not None:
                    classes[k] = got
                continue
            if i in getattr(tr, "questions", {}):
                row = on(tr.questions[i])
                if self.nodes[k].node == "financing_at_floor" and "noraise" in self.nodes[k].context.split("|"):
                    pet = np.where(row["petition"] < 0, BIG, row["petition"])
                    if ((row["day"] < self.days) & (row["day"] < pet) & (row["raise_offer"] > 0)).any():
                        self._raise_more.add(steps[:i])
                row = as_of(row)
                cls = (self.dated_class(d, k, steps, row) if self.deferred_decision(k)
                       else self.canon_get(k, steps))
                if self.deferred_decision(k) and k in self.grouped:
                    if row.get('groups') is None:
                        raise ValueError(f'Missing dated option groups for {k}')
                    cls = group_classes(cls, row['groups'])
                got = self._split((k,), row, lambda key, r, i=i: store(key, steps[:i], r), cls)
                if got is not None:
                    classes[k] = got
                continue
            if i in tr.stays:  # a stay's approval (daily processing): the security sized on the whole path
                got = self._split((k,), as_of(on({**tr.stays[i], "settle_offer": None, "raise_offer": None,
                                                  "sit": tr.situations.get(i), "marks": tr.marks})),
                                  lambda key, r, i=i: store(key, steps[:i], r), self.canon_get(k, steps))
                if got is not None:
                    classes[k] = got
                continue
            info = tr.late[i]
            n = self.nodes[k]
            row = on({"day": tr.day[i], "cash": tr.cash[i], "owed": tr.owed[i], "collateral": tr.collateral[i],
                      "petition": info["petition"], "settle_offer": None, "stay_offer": None,
                      "triggers": info["triggers"], "raise_offer": info["raise_offer"], "sit": tr.situations.get(i),
                      "marks": tr.marks, "groups": tr.groups.get(i)})
            if n.node == "financing_at_floor" and "noraise" in n.context.split("|"):
                pet = np.where(row["petition"] < 0, np.iinfo(np.int64).max, row["petition"])
                if ((row["day"] < self.days) & (row["day"] < pet) & (info["raise_offer"] > 0)).any():
                    self._raise_more.add(steps[:i])
            # once per history and situation class: the decision day's facts, not the future's
            got = self._split((k,), as_of(row), lambda key, r, i=i: store(key, steps[:i], r),
                              self.canon_get(k, steps))
            if got is not None:
                classes[k] = got
        return classes

    @staticmethod
    def late_key(k: str, prefix: tuple, row: dict, *, blob: bytes | None = None) -> tuple:
        """What `_keep_late` keeps a record once per: the node, the prefix and the digest of the record as stored
        (`pack_row`: what a question can read of it)."""
        import xxhash

        return k, prefix, xxhash.xxh3_128_digest(pack_row(row) if blob is None else blob)

    def _keep_late(self, k: str, prefix: tuple, row: dict) -> None:
        """Keep a whole path's record for a node once per distinct record at each prefix that asks it."""
        blob = pack_row(row)
        seen = self.late_key(k, prefix, row, blob=blob)
        if seen not in self._late_seen:
            self._late_seen.add(seen)
            self.facts.setdefault(k, Rows()).append_blob(blob)

    def live(self, n: Node, row: dict) -> np.ndarray:
        """The trajectories where the question's situation holds: the decision falls inside the analysis period,
        before any petition, and (for a question about an unpaid judgment) an amount is still owed."""
        day = row["day"]
        pet = np.where(row["petition"] < 0, np.iinfo(np.int64).max, row["petition"])
        ok = (day < self.days) & (day < pet)  # a path's rows outside its mask are not taken (`masked`)
        # before a pending claim's verdict (I0) nothing is owed by design: the claim, not a judgment, is the situation
        return ok & (row["owed"] > 0) if n.node in OWED and not n.context.startswith("I0") else ok


    # --- the residual questions' state and Jev --------------------------------------------------------------------

    def _date(self, t: float) -> str:
        return fmt(self.review + timedelta(days=int(t) + 1))

    def path_facts(self, n: Node, d: DisputeInstance) -> dict:
        """What code computed for this node, pooled over the paths that reach it, before any Jev answer: the facts
        that exist only because of the dispute and the path's own facts under the ordinary obligations, together
        (`facts_by_source`)."""
        own, common = self.facts_by_source(n, d)
        dates = {**own.pop("contract_dates", {}), **common.pop("contract_dates", {})}
        return {**own, **common, **({"contract_dates": dates} if dates else {})}

    def facts_by_source(self, n: Node, d: DisputeInstance) -> tuple[dict, dict]:
        """The node's path facts split by their source in the code (design 14 May §7.12, orchestrator 28 Sep 2026):
        (1) the dispute's own state: the claim's components, the pending motions, the amount owed, a merged class's
        judgment range, the bond and reduced security, the settlement terms, and the contract dates the dispute
        sets (events.py DISPUTE_TRIGGERS: the appeal deadline, the judgment default's ripe dates); (2) the path under
        the ordinary obligations and background risks: the decision dates, cash and operating need, the raise
        available, the instrument's dated triggers and the instrument's own terms (`obligation_facts`). The ordinary
        view carries (2) from its own engine run and never (1)."""
        from app.analysis.events import DISPUTE_TRIGGERS

        reg = registry_entry(n.question_id)
        remit = self.m["remittitur_scenarios"]["scenarios"].get("remitted", {})
        premise = n.node in ("ts_damages_ruling", "remittitur_accepted") or bool(
            set(n.context.split("|")) & self.remit_classes)
        own: dict = {"components": [self._component(c, remit if premise else {}) for c in d.components]}
        common: dict = {}
        if n.node in MERITS:
            own["pending_motions"] = self._pending(d, n.node)
        if n.question_id in self.no_cash:
            return own, common
        rows = list(self.facts.get(n.key, ()))
        masks = [self.live(n, r) for r in rows]
        if not any(m.any() for m in masks):
            return own, common
        day, cash, owed = (np.concatenate([r[f][m] for r, m in zip(rows, masks, strict=True)])
                           for f in ("day", "cash", "owed"))
        own["amount_owed_at_decision"] = {"p50": usd(int(np.quantile(owed, 0.5))), "max": usd(int(owed.max()))}
        merged = next((self.class_range[c] for c in n.context.split("|") if c in self.class_range), None)
        if merged is not None:  # a merged class: the range of its judgment amounts, beside the amount owed
            own["judgment_after_ruling"] = f"{usd(merged[0])} to {usd(merged[1])}"
            own["amount_owed_at_decision"]["basis"] = ("the lowest judgment in that range, with post-judgment "
                                                       "interest, less any amount collected")
        if reg.get("node") in ("stay_motion", "stay_approved"):
            own["bond_collateral_required"] = usd(int(np.quantile(self._collateral(d, owed, rows, masks), 0.5)))
        if n.node == "stay_approved" and (offer := self._pooled(rows, masks, "stay_offer")) is not None:
            own["reduced_security_offered"] = {"p5": usd(int(np.quantile(offer, 0.05))),
                                               "p50": usd(int(np.quantile(offer, 0.5)))}
        if n.node in ("settlement_offer", "settlement_accept"):
            own["settlement_offer"] = self._settlement(rows, masks)
        split = {keep: [{**r, "triggers": {k: v for k, v in (r.get("triggers") or {}).items()
                                           if (k in DISPUTE_TRIGGERS) == keep}} for r in rows] for keep in (True, False)}
        if n.node in DATED and (dates := self._contract_dates(split[True], masks)):
            own["contract_dates"] = dates
        need = (lambda: np.concatenate([self.draws.basis.need[np.arange(len(r["day"])), np.clip(r["day"], 0, self.days - 1)][m]
                                        for r, m in zip(rows, masks, strict=True)])) if self.draws is not None else None
        fin = next((f for f in d.financing if f.status != "superseded"), None)
        common.update(self.ordinary_facts(n, day, cash, need, self._pooled(rows, masks, "raise_offer"),
                                          (split[False], masks), fin))
        return own, common

    def ordinary_facts(self, n: Node, day, cash, need, eq, dated: tuple, fin) -> dict:
        """The path's facts under the ordinary obligations for a node's type: ONE builder for the forecast
        (`facts_by_source`) and the ordinary view (`bank_facts`), so a question of a given node type gets the same
        fact keys in both views, the dispute-only facts apart (design 14 May §7.12, orchestrator 28 Sep 2026: one fact
        contract per node type). day, cash: the pooled decision days and available cash; need: a callable giving the
        pooled 30-day operating need (None: no draws); eq: the pooled raise offer or None; dated: (rows, masks) whose
        triggers are the instrument's only; fin: the borrower's existing obligation."""
        out = {"decision_date": {"p5": self._date(np.quantile(day, 0.05)), "p50": self._date(np.quantile(day, 0.5)),
                                 "p95": self._date(np.quantile(day, 0.95))},
               "projected_available_cash_at_decision_date": {"p5": usd(int(np.quantile(cash, 0.05))),
                                                             "p50": usd(int(np.quantile(cash, 0.5)))}}
        if n.node in NEED_NODES and need is not None:
            out["operating_need_30_days_at_decision"] = {"p50": usd(int(np.quantile(need(), 0.5)))}
        if n.node == "financing_at_floor" and eq is not None:
            out["equity_raise_available"] = self.raise_facts(eq)
        if n.node in DATED and (dates := self._contract_dates(*dated)):
            out["contract_dates"] = dates
        out.update(self.obligation_facts(fin))
        return out

    def obligation_facts(self, fin) -> dict:
        """The terms of the borrower's existing obligation that the path facts carry (spec §16.3: the same existing
        debt on every path): the notes' principal and their judgment-default clause. Their source is the instrument,
        not the dispute, so the forecast and the ordinary view carry them alike."""
        if fin is None:
            return {}
        return {"notes": {"principal": usd(fin.principal_cents),
                          "judgment_default": notes_default_text(self.m, fin) or "none"}}

    def raise_facts(self, eq: np.ndarray) -> dict:
        """What the company can raise at the cash floor in its situation (code-owned case inputs), and how it arrives."""
        days = int(self.m["parameters"]["raise_days"]["value"])
        return {"p5": usd(int(np.quantile(eq, 0.05))), "p50": usd(int(np.quantile(eq, 0.5))), "max": usd(int(eq.max())),
                "basis": f"sales under the company's existing equity programs, received in equal daily amounts over "
                         f"{days} days from the decision"}

    @staticmethod
    def _pooled(rows: list[dict], masks: list, field: str) -> np.ndarray | None:
        """A per-trajectory amount the chain computes at a step, pooled where the situation holds. Rows whose step
        computes none (all zero: the branch that does not take the step) are left out, unless every row is."""
        got = [(r[field], m) for r, m in zip(rows, masks, strict=True) if r.get(field) is not None and m.any()]
        if not got:
            return None
        some = [(a, m) for a, m in got if a[m].any()]
        return np.concatenate([a[m] for a, m in (some or got)])

    def _settlement(self, rows: list[dict], masks: list) -> dict:
        """The offer the settlement questions decide on: its amount, the company's 30-day operating need on the
        settlement date, and the payment form and schedule of the scenario in force."""
        p = self.m["parameters"]["settlement_date_in_interval"]
        from app.analysis.events import settlement_terms

        at_end = bool(self.sens.get("settlement_date_in_interval"))
        mode, count = settlement_terms(self.m, self.sens)
        monthly = mode == "monthly"
        out: dict = {}
        if (offer := self._pooled(rows, masks, "settle_offer")) is not None:
            out["amount"] = {"p5": usd(int(np.quantile(offer, 0.05))), "p50": usd(int(np.quantile(offer, 0.5)))}
            if mode == "installments":
                each = offer // count
                out["monthly_installment"] = {"p5": usd(int(np.quantile(each, 0.05))),
                                              "p50": usd(int(np.quantile(each, 0.5)))}
        if self.draws is not None and self.draws.basis is not None and not at_end:
            need = self.draws.basis.need
            pd = [np.clip(r["day"] + int(p["value"]), 0, need.shape[1] - 1) for r in rows]
            vals = np.concatenate([need[np.arange(need.shape[0]), x][m] for x, m in zip(pd, masks, strict=True)])
            if vals.size:
                out["thirty_day_operating_need"] = usd(int(np.quantile(vals, 0.5)))
        out["basis"] = ("the company's available cash on the settlement date less its 30-day operating need, floored "
                        "at zero and capped at the amount owed")
        when = "at the end of the current stage of the dispute" if at_end else f"{int(p['value'])} days after the decision"
        if mode == "installments":
            out["payment"] = (f"{count} equal monthly installments, the first on the settlement date ({when}) and "
                              f"one each month after; the claim is released on the settlement date; installments due "
                              f"after {fmt(self.horizon)} fall after the analysis period")
        else:
            out["payment"] = (f"equal monthly payments from the settlement date ({when}) to {fmt(self.horizon)}"
                              if monthly else f"one payment of the full amount on the settlement date, {when}")
        return out

    def _contract_dates(self, rows: list[dict], masks: list) -> dict:
        """The dated contract and procedural triggers the chain computes, on or after the decision, inside the
        analysis period: one date, or the range across these trajectories."""
        from app.analysis.events import BIG

        names = dict.fromkeys(k for r in rows for k in (r.get("triggers") or {}) if k != "coupon_cash")
        out, coupon_cash = {}, []
        for name in names:
            label = TRIGGER_PHRASES.get(name)
            if label is None:
                raise ValueError(f"No plain phrase for contract trigger {name!r}")
            vals, later = [], False
            for r, m in zip(rows, masks, strict=True):
                if name not in (r.get("triggers") or {}):
                    continue
                v, day = r["triggers"][name][m], r["day"][m]
                vals.append(v[(v >= day) & (v < self.days)])
                if name == "coupon" and "coupon_cash" in r["triggers"]:  # the coupon priced off the path
                    coupon_cash.append(r["triggers"]["coupon_cash"][m][(v >= day) & (v < self.days)])
                later |= bool(((v >= self.days) & (v < BIG)).any())
            v = np.concatenate(vals) if vals else np.array([])
            if not v.size:
                continue
            lo, hi = self._date(np.quantile(v, 0.05)), self._date(np.quantile(v, 0.95))
            if "{since}" in label:  # one date: the day execution became available, 60 days before it
                label = label.format(since=f" on {self._date(v.min() - 60)}" if v.min() == v.max() else "")
            text = lo if lo == hi else f"between {lo} and {hi} (median {self._date(np.quantile(v, 0.5))})"
            out[label] = text + (", or after the analysis period ends" if later else "") + (
                self.coupon_amount(np.concatenate(coupon_cash) if coupon_cash else None) if name == "coupon" else "")
        return out

    def coupon_amount(self, cash: np.ndarray | None = None) -> str:
        """The coupon as the engine books it: the amount due and the part paid in cash (a common borrower input);
        `cash`: the cash part on the question's trajectories where the path prices the shares (one amount, or the
        range)."""
        from app.analysis.events import Chain

        fin = self.instrument()
        if fin is None or not fin.coupon_cents or self.draws is None:
            return ""
        total = fin.coupon_cents
        if cash is None:
            cash = Chain(None, self.setup, self.m, self.draws, self.sens, fin=fin).coupon_cash_cents()
        lo, hi = int(np.min(cash)), int(np.max(cash))
        if lo == hi:
            paid = ("paid in cash" if lo == total else "paid in shares" if lo == 0 else
                    f"{usd(lo)} of it paid in cash and {usd(total - lo)} in shares")
        else:
            paid = f"between {usd(lo)} and {usd(hi)} of it paid in cash and the rest in shares"
        return f": {usd(total)} due, {paid}"

    def standard(self, node: str) -> list[str]:
        """The law and contract terms that govern the node's decision, as cited."""
        terms = {k: v for t in self.m["templates"].values() for k, v in t.get("terms_from_instrument", {}).items()}
        return [self.m["rules"][r]["citation"] if r in self.m["rules"] else terms.get(r, r)
                for r in self.spec[node]["standard"]]

    def _component(self, c, remit: dict) -> dict:
        """One judgment component as the record states it, with the source of the passage it cites; the declared
        remittitur scenario beside the compensatory award where a remittitur is the question's premise or its
        outcome."""
        sealed = c.unknown and "sealed" in c.label.lower()
        out = {"component": c.label.split(";")[0].strip() if c.unknown else c.label, "status": c.status,
               "amount": usd(c.amount_cents) if c.amount_cents is not None else
               ("sealed; amount not public" if sealed else "computed by statute" if c.statutory else "unknown")}
        cited = [self.hydrate(self.findings[f])["source"] for f in c.finding_ids if f in self.findings]
        if cited:
            out["source"] = "; ".join(dict.fromkeys(cited))
        if remit.get("amount_cents") and c.kind == "compensatory":
            out["remittitur_scenario"] = f"{usd(remit['amount_cents'])} ({remit['label']}; basis: {remit['basis']})"
        return out

    def _pending(self, d: DisputeInstance, node: str) -> list[dict]:
        """The pending post-trial motions the court's ruling at this node decides: each motion's docket entry, what
        it is, and the close of its briefing."""
        ids = {c.component_id for c in d.components if c.kind in MERITS_KINDS[node]}
        return [{"motion": mo.motion_id, "kind": MOTION_PHRASES[mo.kind], "briefing_closes": fmt(mo.briefing_close)}
                for mo in d.motions
                if set(mo.decides) & ids or (node == "ts_liability_jmol" and mo.kind == "rule_50b")
                or (node == "injunction" and mo.kind == "injunction")]

    def _collateral(self, d: DisputeInstance, owed: np.ndarray, rows: list, masks: list) -> np.ndarray:
        """The cash collateral the law and surety practice require for a stay on each trajectory: the bond (the amount
        owed plus §1961 interest over the appeal) times the collateral share. A pending claim's is the engine's own
        figure on the approval day (events.py `bond_collateral`: the pending §1961 rate, the scenario's share), the
        amount the stay locks; 4.0.0 recomputes it from the amount owed, as recorded."""
        from app.analysis.events import rate_1961_bps

        if d.stage == PENDING:
            return np.concatenate([r["collateral"][m] for r, m in zip(rows, masks, strict=True)])

        p = self.m["parameters"]
        bps = rate_1961_bps(self.m, d.judgment_date) if d.judgment_date else 0
        bond = owed + np.rint(owed * bps / 10_000 * p["bond_forward_interest_years"]["value"])
        return np.rint(bond * p["bond_collateral_share_bps"]["value"] / 10_000)

    @property
    def no_cash(self) -> set[str]:
        return set(load_registry().get("evidence_routing", {}).get("no_cash", []))

    def _routed(self, qid: str) -> set[str]:
        routes = load_registry().get("evidence_routing", {}).get("routes", {})
        return {f for f, qs in routes.items() if qid in qs}

    def _evidence(self, d: DisputeInstance, node: str, read_from: dict[str, list[str]] | None = None
                  ) -> tuple[list[dict], tuple[str, ...], list[dict]]:
        """The accepted findings that supply the question's record items, each passage with the items it supplies,
        and the passages the question's readings were taken from (`read_from`: finding -> the reading labels), each
        with the items the registry says such a passage supplies; and the record items, each marked as in the record
        or not."""
        items = self.spec[node]["record_items"]
        supplies: dict[str, list[str]] = {}
        for item in items:
            for fid in self.slots.get(node, {}).get(item, []):
                if fid in self.findings:
                    supplies.setdefault(fid, []).append(item)
        read_from = {fid: labels for fid, labels in (read_from or {}).items() if fid in self.findings}
        qid = self.spec[node]["residual_question"]
        reading_items = load_registry().get("evidence_routing", {}).get("reading_items", {})
        for fid in read_from:
            its = supplies.setdefault(fid, [])
            for factor in (f.factor_id for f in d.factors if f.decisive and f.decisive.finding_id == fid):
                its += [x for x in reading_items.get(factor, {}).get(qid, []) if x in items and x not in its]
        evidence = [{**self.hydrate(self.findings[fid]), "supplies": its,
                     **({"readings_taken_from_it": read_from[fid]} if fid in read_from else {})}
                    for fid, its in supplies.items()]
        record = [{"item": x, "in_the_record": any(x in its for its in supplies.values())} for x in items]
        return evidence, tuple(supplies), record

    def _readings(self, d: DisputeInstance, qid: str) -> tuple[dict, dict[str, list[str]]]:
        """The present-state readings routed to the question, each with the date and source of the passage it was
        taken from; and, per source finding, the labels of the readings taken from it. A reading taken from no
        passage is not handed on."""
        factors, out, read_from = self._routed(qid), {}, {}
        role = "the company" if d.borrower_role == "debtor" else d.counterparty
        plain = lambda s: s.replace("The payer", role[0].upper() + role[1:]).replace("the payer", role)  # noqa: E731
        for f in d.factors:
            if f.factor_id not in factors or f.decisive is None:
                continue
            src = {"passage_dated": f.decisive.source_date, "source": self._source(f.decisive.finding_id)}
            label = plain(f.label)
            fact = self._fixed_amount(d, f) if f.factor_id == "amount_finality" else ""
            if fact and qid == "forecast_remittitur_accepted":
                continue
            if fact:
                label = "Amount fixed by the court"
                out[label] = {"fact": fact, **src}
            elif f.kind == "present" and f.probability is not None:
                out[label] = {"probability_present": round(f.probability, 3), **src}
            elif f.distribution:
                out[label] = {"distribution": {plain(k): round(v, 3) for k, v in f.distribution.items()},
                              **({"note": "passages of the same date read differently"} if f.conflict else {}), **src}
            if label in out:
                read_from.setdefault(f.decisive.finding_id, []).append(label)
        return out, read_from

    def _source(self, fid: str) -> str:
        """The title of the source a finding's passage comes from."""
        if fid not in self._sources:
            f = self.findings.get(fid)
            self._sources[fid] = self.hydrate(f).get("source", "") if f is not None else ""
        return self._sources[fid]

    def _fixed_amount(self, d: DisputeInstance, f) -> str:
        """Where the record reads the amount as fixed by the court: the judgment's amount and date, as a fact."""
        from app.analysis.events import entered_cents

        levels = self.m["factors"].get(f.factor_id, {}).get("levels", [])
        dist = f.distribution or {}
        if not (levels and dist and d.judgment_date) or max(dist, key=dist.get) != levels[-1]:
            return ""
        return f"{usd(entered_cents(d))}, in the judgment entered {fmt(d.judgment_date)}"

    def state(self, n: Node) -> tuple[dict, tuple[str, ...], dict]:
        d = next(x for x in self.disputes if x.instance_id == n.instance_id)
        rows = list(self.facts.get(n.key, ()))
        tags = [c for c in n.context.split("|") if c and not c.startswith(CLASS_TAG)]
        for tag in n.context.split("|"):
            if tag.startswith(CLASS_TAG):
                context = next((p[3:] for p in tag.split(".") if p.startswith("ctx")), "")
                tags.extend(t for t in bytes.fromhex(context).decode().split("|") if t and t not in tags)
        if n.node == "offering_closes" and any(c.startswith(CLASS_TAG) and c.endswith(".after_failed")
                                               for c in n.context.split("|")):
            tags.append("after_failed")
        return self.built(n, d, tags,
                          lambda: self.path_facts(n, d),
                          (rows, [self.live(n, r) for r in rows]))

    def built(self, n: Node, d: DisputeInstance, tags: list[str], facts, rows: tuple = ([], [])
              ) -> tuple[dict, tuple[str, ...], dict]:
        """A question's state: the case, the decision in its situation (`tags`), the standard, the record items and
        evidence routed to it, the path facts (`facts()`), and the conditions that hold. The forecast and the
        ordinary view (`ordinary_state`) both build their questions here. A 14 May question (profile
        event_forecast) is built from its rows (rows, live masks) by app/disputes/state14.py."""
        if self.event_forecast(n):
            from app.disputes import state14

            return state14.build(self, n, d, tags, *rows, strict=getattr(self, "state_strict", True))
        s = self.spec[n.node]
        readings, read_from = self._readings(d, n.question_id)
        evidence, fids, record = self._evidence(d, n.node, read_from)
        verdict = n.node in VERDICT_NODES
        ctx = [] if verdict else context_phrases(tags, self.class_range, self.labels(d))
        state = {"case": {"as_of": fmt(self.review), "analysis_period_ends": fmt(self.horizon),
                          "company": self.borrower, "counterparty": d.counterparty,
                          "obligation": f"{self.m['natures'].get(d.nature, d.nature)}, {d.order_reference}"},
                 "question": {"actor": s["actor"], "decision": s["decision"], "branches": list(n.branches),
                              "timing": s["timing"], "context": ctx},
                 "standard": self.standard(n.node),
                 "record_items": record, "path_facts": facts(),
                 "assumptions": list(n.assumptions), "evidence": evidence, "readings": readings}
        if verdict:
            state["question"].update(self.verdict_context(n))
        return state, fids, readings

    async def judge(self, judge: ForecastJudge) -> dict[str, Judgment]:
        async def one(n: Node) -> Judgment:
            st, fids, readings = self.state(n)
            o = await judge.forecast(n.question_id, st, (n.instance_id, *fids), n.branches)
            dist = answer_distribution(n.key, n.branches, o)
            return Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                            event=n.event, assumptions=n.assumptions, window=n.window, distribution=dist,
                            confidence=o.confidence, finding_ids=fids, readings=readings,
                            evidence=state_evidence(st),
                            observation_id=o.observation_id, path_facts=st.get("path_facts", st.get("situation")))

        results = await asyncio.gather(*(one(n) for n in self.nodes.values() if n.key not in self.classed))
        return {j.key: j for j in results}


@dataclass(frozen=True)
class _S:
    steps: tuple = ()
    edges: tuple = ()
    cls: str = "entered"  # amount class label used in node contexts
    stayed: bool = False
    appealed: bool = False
    early: bool = False
    a4: str = "open"  # open | seek | closed
    notes_due: bool = False  # the notes are accelerated or the repurchase is due, unpaid, and nobody has filed
    floor: str = "open"  # the state-triggered decisions: open (floor not asked) | cash_out (floor asked) | done
    late: tuple = ()  # (node key, step index) of each floor decision asked: its facts come from the whole path
    # the equity model's distress loop (QUESTIONS §4.4): the next cash-floor decision's index (D7 at the k-th fall
    # below the need after a recovery), D8 and §3.3 open or done, and whether an offering on the path did not close
    k: int = 1
    out: str = "open"
    np: str = "open"
    failed: bool = False
    resp: str = "none"  # a pending claim's last D2 answer that leaves the response open: none | offer

    def add(self, step, edge, **kw) -> _S:
        """edge: one (node key, branch), None, or a list of them (a grouped step: one edge per option group)."""
        new = tuple(edge) if isinstance(edge, list) else ((edge,) if edge else ())
        return _S(self.steps + (step,), self.edges + new,
                  **{**{k: getattr(self, k) for k in ("cls", "stayed", "appealed", "early", "a4", "notes_due", "floor",
                                                      "late", "k", "out", "np", "failed", "resp")}, **kw})


def _label(c: str) -> str:
    return c.split(":")[0] + (c.split(":")[1] if c.startswith("amt:") else "") + (
        "_retrial" if c.endswith(":retrial") else "")


@dataclass
class _Watch:
    """A question whose no-event branch is walked first (QUESTIONS_20240514 §1 Depth; spec §16.4): it is asked only if
    a later question on that branch reads what its event would change. marks: condition -> the day it would hold from
    on each trajectory had the event occurred (a later question's situation tags read them, `_Walk.situation`);
    nodes: later questions that read the event directly; walks: walker structure the event changes (`_Walk._reads`)."""
    marks: dict
    nodes: frozenset = frozenset()
    walks: frozenset = frozenset()
    read: bool = False


def _complement(conj: tuple, branches: dict) -> list[list[tuple[str, str]]]:
    """Disjoint conjunctions covering exactly the answers no conjunction of `conj` covers (a composite's 'no'), split
    node by node over each node's branches."""
    if not conj:
        return [[]]
    if any(not c for c in conj):  # an empty conjunction covers everything
        return []
    k = conj[0][0][0]
    out = []
    for b in branches[k]:
        rest = tuple(tuple(e for e in c if e[0] != k) for c in conj if dict(c).get(k, b) == b)
        out += [[(k, b), *x] for x in _complement(rest, branches)] if rest else [[(k, b)]]
    return out


def _expand(edges, branches: dict) -> list[list[tuple[str, str]]]:
    """A product of edges as disjoint conjunctions of node answers (a composite's 'yes': its conjunctions; its 'no':
    their complement)."""
    opts = []
    for k, b in edges:
        if k.startswith(COMPOSITE):
            conj = _conjunctions(k)
            opts.append([list(c) for c in conj] if b == "yes" else _complement(conj, branches))
        else:
            opts.append([[(k, b)]])
    return [[e for part in combo for e in part] for combo in itertools.product(*opts)]


def merge_equivalent(paths: list[DisputePath], keys: list, branches: dict) -> list[DisputePath]:
    """QUESTIONS_20240514 §1 Depth, the backstop: paths with the same draw mask, the same event cash to the horizon and
    the same verdict class and post-trial ruling (`_Walk.equivalence`) are one path. Its edges are those every member
    carries, and one composite edge whose conjunctions are the rest of each member's edges (disjoint: the members
    are distinct paths of one tree), so its probability is the members' sum by the chain rule (`Dist.__missing__`).
    It keeps the first member's steps and outcome (the page's path text)."""
    groups: dict = {}
    for i, k in enumerate(keys):
        groups.setdefault(k, []).append(i)
    out = []
    for i, k in enumerate(keys):
        if groups[k][0] != i:
            continue
        for g in _class_compatible(paths, groups[k]):
            out.append(_merged(paths, g, branches))
    return out


def _class_compatible(paths: list[DisputePath], g: list[int]) -> list[list[int]]:
    """The members of one equivalence group split where they record different classes of a question they share
    (`DisputePath.classes`): only those merge (a merged path's probability reads each question's class per draw)."""
    out: list[tuple[dict, list[int]]] = []
    for j in g:
        cj = {k: (t, c) for k, t, c in paths[j].classes}
        for seen, members in out:
            if all(seen[k] == v for k, v in cj.items() if k in seen):
                seen.update(cj)
                members.append(j)
                break
        else:
            out.append((dict(cj), [j]))
    return [m for _, m in out]


def _merged(paths: list[DisputePath], g: list[int], branches: dict) -> DisputePath:
    from collections import Counter

    if len(g) == 1:
        return paths[g[0]]
    common = Counter(paths[g[0]].edges)
    for j in g[1:]:
        common &= Counter(paths[j].edges)
    keep, left = [], Counter(common)
    for e in paths[g[0]].edges:
        if left[e] > 0:
            keep.append(e)
            left[e] -= 1
    conj = []
    for j in g:
        rest = list((Counter(paths[j].edges) - common).elements())
        if not rest:
            raise ValueError("a merged path's edges are a subset of another member's: the members are not disjoint")
        conj += _expand(rest, branches)
    classes = {k: (k, t, c) for j in g for k, t, c in paths[j].classes}
    return replace(paths[g[0]], edges=(*keep, (composite(conj), "yes")),
                   classes=tuple(classes[k] for k in sorted(classes)))


class _Walk:
    # the grouped decisions forked per option group (the reference the per-draw classes are checked against)
    GROUP_FORK = __import__("os").environ.get("SLOPE_GROUP_FORK") == "1"

    def __init__(self, fc: Forecaster, d: DisputeInstance) -> None:
        self.fc, self.d, self.out = fc, d, []
        self._population: np.ndarray | None = None  # current chronological ordering population
        self._masks = _DepthCache(fc.SIBLINGS)  # a prefix ending in a grouped step -> its trajectories (`mask_of`)
        self._watch: list[_Watch] = []  # the questions whose no-event branch is being walked (QUESTIONS §1 Depth)
        self.keys: list = []  # per emitted path, its financial-equivalence key (`equivalence`)
        self.fin = next((f for f in d.financing if f.status != "superseded"), None)
        self.N = fc.days
        self.pend = d.stage == PENDING
        self.resp = "judgment_response" if self.pend else "debtor_response"  # the template's response node
        self.quiet = "none" if self.pend else "neither"  # the branch that books nothing
        # the branches after which the response is asked again at the next milestone (QUESTIONS §4.4 D2: 'none' and
        # an initiation; no state of seeking a sale or financing exists in 14 May)
        self.seek = "none" if self.pend else "seek_sale_or_financing"
        self.again = ("none", "initiate_offering") if self.pend else (self.seek,)
        if self.fin is not None and fc.reach is not None and self.fin.principal_cents <= fc.reach:
            raise NotImplementedError("Paying the notes is arithmetically possible on some trajectory; the chains remove "
                                      "that branch only when the principal exceeds cash on every trajectory")

    def scoped(self, population: np.ndarray | None, then):
        """Continue one ordering population, restoring branch-local masks and question classes afterwards."""
        previous, masks = self._population, self._masks
        canonical, classes, at = self.fc._qcanon, self.fc._qcls, self.fc._rec_at
        self.fc._qcanon = {k: list(v) for k, v in canonical.items()}
        self.fc._qcls = {k: list(v) for k, v in classes.items()}
        self._population, self._masks = population, _DepthCache(self.fc.SIBLINGS)
        try:
            return then()
        finally:
            self._population, self._masks = previous, masks
            self.fc._qcanon, self.fc._qcls, self.fc._rec_at = canonical, classes, at

    # helpers
    def node(self, name, *ctx, s: _S | None = None, probe=None, assumptions=(), branches=None, groups=None):
        """The question in its situation: the context tags given, plus the conditions its actor weighs (the model
        node's `situation`) that hold at the decision on every trajectory. groups: per draw, the option group it is
        asked of (-1: not asked): a grouped question, each draw in its group's class (`group_classes`)."""
        for w in self._watch:  # a later question that reads the watched event directly
            w.read |= name in w.nodes
        tags = self.situation(s, probe, name, ctx) if s is not None else ()
        if self.d is not None:
            n = self.fc.new_node(self.d, name, *ctx, assumptions=assumptions, branches=branches)
            if groups is not None or (self.fc.CLASSES and self.pend and self.fc.event_forecast(n)
                                      and n.question_id not in self.fc.no_cash):
                tags = ()
        if groups is not None:
            from app.analysis.events import group_branches

            # a grouped question: every answer it offers any group (`branches` as given: this prefix's groups'
            # answers, the path's edges); each class node carries its group's own (`Forecaster.class_key`)
            branches = group_branches(name, 3)
        k = self.fc.node(self.d, name, *ctx, *tags, assumptions=assumptions, branches=branches)
        if groups is not None:
            self.fc.grouped.add(k)
            if s is not None and self.deferred_dated(k):
                # Retain enumeration's answer domain if this decision later
                # becomes inactive. Active conditioning still comes from its
                # completed before-answer record, never these domain-only tags.
                self.fc.canon_put(k, s.steps, group_classes(None, groups))
        if s is not None and probe is not None and self.fc._classified(k) and not self.deferred_dated(k) \
                and not any(e[0] == s.steps for e in self.fc._qcanon.get(k, ())):
            # QUESTIONS §1 Grouping: each draw's class as the question is asked, on the branch that books nothing (the
            # state before the decision); every branch's paths and rows read it (`Forecaster.canon_get`)
            at = probe if isinstance(probe[0], tuple) else (probe,)
            row = as_of(self.fc.row_of(self._facts(s.steps + at)))
            if groups is not None:
                cls = group_classes(self.context_class(
                    self.fc.question_class(self.fc.nodes[k], row, groups >= 0), row, s, name, ctx), groups)
            else:
                cls = self.context_class(
                    self.fc.question_class(self.fc.nodes[k], row, self.fc.live(self.fc.nodes[k], row)),
                    row, s, name, ctx)
            self.fc.canon_put(k, s.steps, cls)
        return k

    def deferred_notes(self, key: str) -> bool:
        """Pending notes decisions read their completed, before-action path."""
        from app.disputes.notes import NAMES

        return self.d is not None and self.pend and self.fc.nodes[key].node in NAMES

    def deferred_dated(self, key: str) -> bool:
        """These decisions occur after their traversal prefix: bind their own dated record."""
        return self.fc.deferred_decision(key)

    def _reads(self, walk: str) -> None:
        """A later question asked where the walk's structure depends on a watched event (`_Watch.walks`)."""
        for w in self._watch:
            w.read |= walk in w.walks

    def _watched(self, s: _S, no: tuple, w: _Watch, then) -> bool:
        """Walk the no-event branch `no` (no edge) with `w` watching; return whether a later question read the event.
        Where it did, the question is asked: the caller adds its node and walks the event branch, and the no-event
        branch's paths get its edge (`_edge_after`)."""
        self._watch.append(w)
        try:
            then(s.add(no, None))
        finally:
            self._watch.pop()
        return w.read

    def _edge_after(self, i0: int, at: int, edge: tuple) -> None:
        """Give the paths emitted since `i0` the edge `edge` at position `at` (the question's own place on them)."""
        for i in range(i0, len(self.out)):
            p = self.out[i]
            self.out[i] = replace(p, edges=p.edges[:at] + (edge,) + p.edges[at:],
                                  classes=p.classes + self._classes_of((edge,), p.steps, path_mask(p, self.fc.draws.n),
                                                                       {}, {c[0] for c in p.classes}))

    def _classes_of(self, edges, steps, mask, late: dict, have=frozenset()) -> tuple:
        """The `DisputePath.classes` entries of the classed questions among `edges` on the path `steps` (on its draws
        `mask`), each from the question's own record (`late`: a state-triggered question's, from the whole path)."""
        out, seen = [], set(have)
        for key, _ in edges:
            for k in sorted(atoms(key)):
                if k in seen or not self.fc._classified(k):
                    continue
                seen.add(k)
                cls = late.get(k) if self.deferred_dated(k) else self.fc.canon_get(k, steps)
                if cls is None:
                    cls = late[k] if k in late else self.fc._qcls_get(k, steps)
                if cls is not None and k in self.fc.grouped and self.deferred_dated(k):
                    domain = self.fc.canon_get(k, steps)
                    if domain is not None:
                        inactive = (cls == '') & (domain != '')
                        if inactive.any():
                            cls = np.where(inactive, domain, cls)
                            for tag in set(domain[inactive]):
                                self.fc.class_key(k, tag)
                if cls is not None:
                    out.append(class_entry(k, cls, mask))
        return tuple(out)

    def situation_conditions(self, name: str) -> list[str]:
        conds = list(self.fc.spec[name].get("situation", []))
        # These routes previously encoded a pending motion in the raw node key.
        # Its existence and denial now come from the dated before-answer record.
        if self.pend and name in ("enforce_after_final", "judgment_response") and "stay_moved" not in conds:
            conds.append("stay_moved")
        if self.pend and "stay_moved" in conds:
            conds.append("stay_denied")
        return conds

    def situation(self, s: _S, probe, name: str, ctx) -> tuple[str, ...]:
        conds = self.situation_conditions(name)
        if not conds:
            return ()
        at = probe if isinstance(probe[0], tuple) else (probe,)  # one probe step, or several
        tr = self._trace(s.steps + at)
        out = self._tags(s, conds, ctx, tr, at)
        for w in self._watch:  # would the event, had it occurred, change the question's situation?
            if not w.read and tr.marks is not None and set(w.marks) & set(conds):
                cf = {**tr.marks, **{c: np.minimum(tr.marks[c], v) for c, v in w.marks.items()}}
                w.read = self._tags(s, conds, ctx, replace(tr, marks=cf), at) != out
        if self.d is not None and self.pend and any(
                not w.read and set(conds) & set(w.marks) for w in self._watch):
            row = {"day": tr.day[-1], "petition": tr.petition, "marks": tr.marks}
            live = (row["day"] < self.N) & ((tr.petition < 0) | (row["day"] < tr.petition))
            before = self.contexts(s, ctx, row, conds)
            for watch in self._watch:
                if watch.read or not set(conds) & set(watch.marks):
                    continue
                marks = {**tr.marks, **{c: np.minimum(tr.marks[c], v) for c, v in watch.marks.items()}}
                after = self.contexts(s, ctx, {**row, "marks": marks}, conds)
                if np.any(live & (before != after)):
                    watch.read = True
        return out

    def contexts(self, s: _S, ctx, row: dict, conds: list) -> np.ndarray:
        """The existing context vocabulary, evaluated separately for each dated situation."""
        from app.analysis.events import BIG

        day = row["day"]
        bits = np.zeros(len(day), dtype=np.int64)
        for i, condition in enumerate(conds):
            bits |= (np.asarray(row["marks"].get(condition, np.full(len(day), BIG))) <= day).astype(np.int64) << i
        out = np.full(len(day), "", dtype=object)
        live = (day < self.N) & ((row["petition"] < 0) | (day < row["petition"]))
        values, inverse = np.unique(bits[live], return_inverse=True)
        vocabulary = []
        for bit in values:
            held = {c for i, c in enumerate(conds) if bit & (1 << i)}
            never = set(conds) - held
            vocabulary.append("|".join(self._context_tags(s, conds, ctx, held, never)))
        out[live] = np.asarray(vocabulary, dtype=object)[inverse]
        sit = row.get("sit") or {}
        if "ruled" in conds and "motions_filed" in sit:
            pending = (sit["motions_filed"] <= day) & (sit["ruling"] > day)
            open_ = (sit["entry"] <= day) & (sit["motions_deadline"] > day) & ~pending
            for tag, on in (("motions_pending", pending), ("motions_open", open_)):
                on = live & on
                out[on] = np.array(["|".join(filter(None, (str(text), tag))) for text in out[on]], dtype=object)
        status = sit.get("stay_status")
        if "stay_moved" in conds and status is not None:
            for value in ("pending", "denied", "approved", "resolved"):
                on = live & (status == value)
                out[on] = np.array(["|".join(filter(None, (str(text), "stay_" + value))) for text in out[on]],
                                   dtype=object)
        return out

    def context_class(self, cls, row: dict, s: _S, name: str, ctx):
        conds = self.situation_conditions(name)
        if cls is None or not conds:
            return cls
        text = self.contexts(s, ctx, row, conds)
        out = cls.astype(object)
        for value in set(text[cls != ""]):
            on = (cls != "") & (text == value)
            out[on] = np.strings.add(cls[on].astype(str), ".ctx" + value.encode().hex()).astype(object)
        return out

    def _tags(self, s: _S, conds: list, ctx, tr: _Prefix, at) -> tuple[str, ...]:
        held, never = self.fc.situation(self.d, s.steps + at, conds, tr=tr)
        return self._context_tags(s, conds, ctx, held, never)

    def _context_tags(self, state: _S, conds: list, ctx, held: set, never: set) -> tuple[str, ...]:
        out = []
        for c in conds:
            if c == "ruled" and self.pend and state.cls in NO_JUDGMENT:
                if state.cls not in ctx:  # no money judgment on the path: that is the situation
                    out.append(state.cls)
            elif c == "ruled":
                if held & {"settled", "paid"}:  # the judgment is no longer owed: its amount is not the situation
                    continue
                if c in held and state.cls not in ctx:
                    out.append(state.cls)
                elif c in never and not self.pend and not any(x in INTERVAL_PHRASES for x in ctx):
                    out.append("motions_pending")
            elif c not in held:
                continue
            elif c == "stay_moved" and ("stay_pending" in ctx or "stayed" in held or "I4" in ctx):
                continue
            elif (c == "stayed" and "I4" in ctx) or (c == "seeking" and "after_seek" in ctx):
                continue
            else:
                out.append(c)
        return tuple(out)

    def _raw(self, steps, full: bool = False) -> _Prefix:
        """The prefix's trace on every trajectory, in this walk's view (the forecast's dispute; the ordinary view
        overrides it). Settled to the last step's decision day (`Forecaster.trace`) unless `full`. Light: the walk's
        structure reads none of the facts only a recorded question reads (`_facts` computes those). Day-only traces
        are computed on the path's trajectories alone (`_rows`)."""
        return self.fc.trace(self.d, steps, full, light=True, rows=None if full else self._rows(steps))

    def _rows(self, steps) -> tuple:
        """Per step, the trajectories the path follows after it (`mask_of` at each grouped step; None: every draw).
        SLOPE_ROWS=0: None (every trace on every draw, the reference the row subsets are checked against)."""
        if ROWS_OFF:
            return None
        out, m = [], getattr(self, "_population", None)
        for i, st in enumerate(steps):
            if st[2].startswith("@"):
                m = self.mask_of(steps[:i + 1])
            out.append(m)
        return tuple(out)

    def _trace(self, steps, full: bool = False) -> _Prefix:
        """The prefix's trace on the path's trajectories (`mask_of`, `masked`): every read the walk makes of it."""
        return masked(self._raw(steps, full), self.mask_of(steps))

    def _facts(self, steps) -> _Prefix:
        """`_trace` for a recorded fact: computed, never a sibling's (`Forecaster._served`)."""
        self.fc._rec_at = tuple(steps)  # the prefix the facts are recorded at (`Forecaster._qcls_put`)
        return masked(self.fc.trace(self.d, steps, real=True, rows=self._rows(steps)), self.mask_of(steps))

    # --- within-path grouping (Owen's ruling, 29 Sep 2026) ----------------------------------------------------------
    def quiet_of(self, node: str) -> str:
        """The branch that books nothing at a grouped question (its probe)."""
        from app.analysis.events import RESPONSES

        if node == "settle":
            return "no"
        return self.quiet if node in RESPONSES else "neither"  # the ordinary walk asks no response

    def walk_groups(self, steps) -> np.ndarray:
        """Per trajectory, the option group (events.Chain.option_group) the last step is asked of, or -1 where it is
        not asked: outside the horizon, after a petition, or not in the question's situation."""
        tr = self._raw(steps)
        if steps[-1][0] == "settle":
            return ((tr.day[-1] < self.N) & (tr.settle_offer > 0)).astype(np.int8)
        if tr.groups is None:
            return np.full(len(tr.petition), -1, dtype=np.int8)
        t = tr.day[-1]
        pet = np.where(tr.petition < 0, np.iinfo(np.int64).max, tr.petition)
        return np.where((t < self.N) & (t < pet) & (tr.groups >= 0), tr.groups, -1).astype(np.int8)

    def mask_of(self, steps) -> np.ndarray | None:
        """The trajectories the path follows (None: every draw): at each grouped step ('@<group>=<answer>'), those of
        the group it took, among its parent's. The groups are measured on the parent prefix with the question's probe,
        so a step's children partition its parent's trajectories."""
        from app.analysis.events import step_group

        steps = tuple(steps)
        j = next((i for i in range(len(steps) - 1, -1, -1) if steps[i][2].startswith("@")), None)
        population = getattr(self, "_population", None)
        if j is None:
            return population
        key = steps[:j + 1]
        m = self._masks.get(None, key)
        if m is None:
            node, ctx, branch = steps[j]
            g = np.isin(self.walk_groups(steps[:j] + ((node, ctx, self.quiet_of(node)),)), step_group(branch))
            parent = self.mask_of(steps[:j])
            m = self._masks.put(None, key, g if parent is None else parent & g)
        return m if population is None else m & population

    def rec(self, k: str, steps) -> None:
        """Record a question's facts from the prefix `steps` (its last step is the question's own day)."""
        if not self.deferred_dated(k):
            self.fc.record((k,), self._facts(steps))

    def inside(self, steps) -> bool:
        """Whether the last step's decision falls inside the horizon, before any petition, on some trajectory."""
        tr = self._trace(steps)
        t = tr.day[-1]
        pet = np.where(tr.petition < 0, np.iinfo(np.int64).max, tr.petition)
        return bool(((t < self.N) & (t < pet)).any())

    def arises(self, s: _S, step) -> bool:
        """Whether the decision falls inside the horizon on some trajectory; for a pending claim (4.1.0), whose cash
        floor may be asked first, also before any petition (`inside`)."""
        return self.inside(s.steps + (step,)) if self.pend else self.fc.arises(self.d, s.steps, step)

    def take(self, s: _S, step, edge, keys=(), **kw) -> _S:
        """Add a step; record its path facts (from the trace of the prefix plus this step) for the nodes it asks."""
        if keys:
            self.fc.record(keys, self._facts(s.steps + (step,)))
        return s.add(step, edge, **kw)

    def court(self, s: _S, key: str, ctx: str) -> None:
        """A court's ruling on a motion gets the facts of its own day (events.py court_order): the stay's approval, with
        the security measured that day, or the registration order; the motion question keeps the motion day."""
        self.fc.record((key,), self._facts(s.steps + (("court_order", ctx, ""),)))

    def stay_court(self, s: _S, key: str, ctx: str) -> _S:
        """The stay-approval question's facts. Under daily processing they come from each whole path (events.py
        `_stay_effects`: the security is sized on the approval day's balance after every event dated before it, whatever
        the walk order, and is what the engine locks); otherwise from the court's own day on the prefix (`court`)."""
        if self.fc.setup is not None and self.fc.setup.cash_processing == "daily":
            return replace(s, late=s.late + ((key, len(s.steps)),))
        self.court(s, key, ctx)
        return s

    def binary(self, s: _S, node: str, ctx: str, parts: list[list[tuple[str, str]]], keys, then_yes, then_no):
        k = composite(parts)
        then_yes(self.take(s, (node, ctx, "yes"), (k, "yes"), keys))
        then_no(self.take(s, (node, ctx, "no"), (k, "no"), keys))

    def stay_answers(self, s: _S, ctx: str, motion: str, approval: str, then_yes, then_denied, then_no):
        """A denied motion still existed before the court ruled on it.

        Keep that history separate wherever intervening decisions read the motion;
        the court's answer must not choose their before-answer circumstances.
        """
        if not self.pend:
            s = self.stay_court(s, approval, f"stay_{ctx}")
            return self.binary(s, "stay", ctx, [[(motion, "yes"), (approval, "yes")]], (motion,),
                               then_yes, then_no)
        then_no(self.take(s, ("stay", ctx, "no"), (motion, "no"), (motion,)))
        moved = self.stay_court(s, approval, f"stay_{ctx}")
        for answer, branch, then in (("yes", "yes", then_yes), ("no", "denied", then_denied)):
            edge = composite([[(motion, "yes"), (approval, answer)]])
            # The motion is one before-answer question. Its neutral record above
            # also represents the moved branches; their completed stay row is the
            # court's later decision, not another motion record.
            then(self.take(moved, ("stay", ctx, branch), (edge, "yes"), ()))

    def settle(self, s: _S, interval: str, then_no, then_yes=None) -> None:
        """A settlement exists only where its amount (cash above the 30-day need, capped at the amount owed) is
        positive: where it is zero on every trajectory the question does not arise."""
        probe = ("settle", interval, "no")
        tr = self._trace(s.steps + (probe,))
        feasible = (tr.day[-1] < self.N) & (tr.settle_offer > 0)
        if not feasible.any():
            return then_no(s)
        if interval == "I3":
            self._reads("unstayed")  # the window after the appeal deadline: asked on a path not stayed before the ruling
        if self.first(s, probe, lambda y: self.settle(y, interval, then_no, then_yes)):
            return
        a3 = self.node("settlement_offer", interval, s.cls, s=s, probe=probe)
        from app.analysis.events import settlement_terms

        mode, count = settlement_terms(self.fc.m, self.fc.sens)
        terms = "the company offers to settle for its available cash above its 30-day operating need" + (
            f", paid in {count} equal monthly installments from the settlement date" if mode == "installments" else "")
        q4 = self.node("settlement_accept", interval, s.cls, s=s, probe=probe, assumptions=(terms,))
        accepted = then_yes if then_yes is not None else lambda y: self.tail(y, "settled")
        keys = (a3, q4)
        if self.pend and interval == 'I3':
            s = replace(s, late=s.late + ((a3, len(s.steps)), (q4, len(s.steps))))
            keys = ()
        mask = self.mask_of(s.steps)
        on = np.ones(len(feasible), dtype=bool) if mask is None else mask
        if self.fc.equity and (on & ~feasible).any():
            then_no(s.add(("settle", interval, "@0=no"), None))
            k = composite([[(a3, "yes"), (q4, "yes")]])
            accepted(self.take(s, ("settle", interval, "@1=yes"), (k, "yes"), keys))
            then_no(self.take(s, ("settle", interval, "@1=no"), (k, "no"), keys))
        else:
            self.binary(s, "settle", interval, [[(a3, "yes"), (q4, "yes")]], keys, accepted, then_no)

    def cx(self, s: _S) -> tuple[str, ...]:
        """A pending claim's nodes that 4.0.0 keys without an amount class carry the verdict branch."""
        return (s.cls,) if self.pend else ()

    def run(self) -> list[DisputePath]:
        d, s = self.d, _S()
        if self.pend:  # template pending_money_claim: settlement before the verdict, then the verdict
            self.settle(_S(cls="claimed"), "I0", self.verdict)
            return self.out
        if d.stage == "post_trial" and any(m.kind in ("rule_50b", "rule_52b", "rule_59a", "rule_59e")
                                           for m in d.motions):
            self.settle(s, "I1", self.q1)
        else:
            cls = self._entered_class()
            self.post(_S(cls=_label(cls), stayed=d.stage == "appeal_pending",
                         appealed=d.stage in ("appeal_filed", "appeal_pending")))
        return self.out

    def _entered_class(self) -> str:
        from app.analysis.events import entered_cents

        total = entered_cents(self.d)
        lower = self.fc.m["parameters"]["bond_collateral_share_bps"]["lower"] / 10_000
        return f"beyond:{total}:0" if self.fc.reach is not None and total * lower > self.fc.reach else f"amt:{total}:0"

    # a pending claim: the verdict (J1 composites over the verdict form), entry, post-trial motions
    def verdict(self, s: _S) -> None:
        branches = self.fc.m["templates"]["pending_money_claim"]["verdict_branches"]
        if self.first(s, ("verdict", "I0", next(iter(branches))), self.verdict):
            return
        for b, parts in self.fc.verdict_classes(self.d).items():
            # the class: no award, or the award booked ('award:<booked>:<band>'), named by its booked amount
            cls = b if b in branches else "award" + b.split(":")[1]
            y = s.add(("verdict", "I0", b), (composite(parts), "yes"), cls=cls)
            if b not in branches or branches[b]["judgment"]:
                self.entry(y)
            else:  # no money judgment: no enforcement, stay, registration or judgment-default node on the path
                self.tail(y, "no_judgment")

    def entry(self, s: _S) -> None:
        """The company's response on the day the judgment is entered (D2), then its post-trial motions (D1)."""
        if not self.arises(s, (self.resp, "entry", self.quiet)):
            return self.entry_settlement(s)
        self.a4(s, "entry", self.entry_settlement, lambda y: self.emit(y, "petition"))

    def entry_settlement(self, s: _S) -> None:
        self.settle(s, "Ientry", self.motions)

    def motions(self, s: _S) -> None:
        probe = ("post_trial_motions", "", "no")
        if not self.arises(s, probe):
            return self.post(s)
        if self.first(s, probe, self.motions):
            return
        k = self.node("post_trial_motions", s.cls, s=s, probe=probe,
                      assumptions=("a money judgment is entered on the verdict",))
        self.settle(self.take(s, ("post_trial_motions", "", "yes"), (k, "yes"), (k,)), "I1", self.q1)
        self.post(self.take(s, probe, (k, "no"), (k,)))

    # I1: before the post-trial ruling
    def q1(self, s: _S) -> None:
        if self.first(s, ("execute_pre_ruling", "I1", "no"), self.q1):
            return
        no, yes = ("execute_pre_ruling", "I1", "no"), ("execute_pre_ruling", "I1", "yes")
        if self.pend and self._q1_opens_nothing(s, yes):
            # QUESTIONS §4.3 C1, §1 Depth: the stay it opens is approved and its levy falls only after the horizon (or
            # a petition); it is asked only where a later question reads the execution, the stay motion or the stay
            i0 = len(self.out)
            y, st = self._raw(s.steps + (yes,), True), self._raw(s.steps + (yes, ("stay", "I1", "yes")), True)
            w = _Watch({"executing": y.marks["executing"], "stay_moved": st.marks["stay_moved"]},
                       nodes=frozenset({"enforce_after_final"}), walks=frozenset({"unstayed"}))
            if not self._watched(s, no, w, self.ripe_i1):
                return  # not asked: the path books the no-execution branch (the step, no edge)
            k = self.node("execute_pre_ruling", "I1", *self.cx(s), assumptions=("post-trial motions are pending",))
            self.fc.record((k,), self._facts(s.steps + (yes,)))
            self.fc.record((k,), self._facts(s.steps + (no,)))
            self._edge_after(i0, len(s.edges), (k, "no"))
            return self.stay_i1(s.add(yes, (k, "yes")))
        k = self.node("execute_pre_ruling", "I1", *self.cx(s), assumptions=("post-trial motions are pending",))
        self.stay_i1(self.take(s, ("execute_pre_ruling", "I1", "yes"), (k, "yes"), (k,)))
        self.ripe_i1(self.take(s, ("execute_pre_ruling", "I1", "no"), (k, "no"), (k,)))

    def _q1_opens_nothing(self, s: _S, yes: tuple) -> bool:
        """On the path's trajectories where the execution falls inside the horizon before any petition, what the
        execution opens books nothing inside it: the stay (D4, J3) is approved, and its security locked, only on or
        after the horizon's end or a petition, and so is the levy an early registration (J4) would bring (so no
        levy-day response either)."""
        from app.analysis.events import BIG, pval

        q = self._trace(s.steps + (yes,), True)  # the petition after the execution day bounds them
        t = q.day[-1]
        pet = np.where(q.petition < 0, BIG, q.petition)
        on = (t < self.N) & (t < pet)
        end = np.minimum(self.N, pet)
        approval = self._trace(s.steps + (yes, ("court_order", "stay_I1", ""))).day[-1]
        order = self._trace(s.steps + (yes, ("court_order", "registration_I1", ""))).day[-1]
        levy = order + int(pval(self.fc.m, "levy_lag_days", self.fc.sens.get("levy_lag_days", False)))
        return bool(((approval >= end) & (levy >= end))[on].all())

    def stay_i1(self, s: _S) -> None:
        probe = ("stay", "I1", "no")
        if self.first(s, probe, self.stay_i1):
            return
        a1 = self.node("stay_motion", "I1", s.cls, s=s, probe=probe,
                       assumptions=("the creditor executes before the ruling",))
        j8 = self.node("stay_approved", "I1", s.cls, s=s, probe=probe, assumptions=("the company moves for a stay",))
        self.stay_answers(s, "I1", a1, j8,
                          lambda y: self.j9_stayed(replace(y, stayed=True)), self.j9_i1, self.j9_i1)

    def j9_stayed(self, s: _S) -> None:
        """A stay is effective only on approval: early registration and its levy can come before it, and no
        levy after it (events.py levy). Asked where the levy moves cash on some trajectory."""
        yes, no = (("registration_early", "I1", b) for b in ("yes", "no"))
        if not self.fc.moves_cash(self.d, s.steps, yes, no):
            return self.ripe_i1(s)
        self.j9_i1(s)

    def j9_i1(self, s: _S) -> None:
        """Cash is reachable before the ruling only through early registration. Its levy is the act that confronts
        the debtor; without the order nothing reaches cash before the ruling, and the debtor's response waits for it."""
        if self.first(s, ("registration_early", "I1", "no"), self.j9_i1):
            return
        k = self.node("registration_early", "I1", *self.cx(s), s=s, probe=("court_order", "registration_I1", ""),
                      assumptions=("the creditor executes before finality",))
        if self.pend:
            s = replace(s, late=s.late + ((k, len(s.steps)),))
        else:
            self.court(s, k, "registration_I1")
        self.a4_i1(self.take(s, ("registration_early", "I1", "yes"), (k, "yes"), early=True))
        self.ripe_i1(self.take(s, ("registration_early", "I1", "no"), (k, "no")))

    def a4_i1(self, s: _S) -> None:
        """The debtor's response on the levy day (order + levy_lag_days), where it falls inside the horizon, before stay approval and
        before the ruling on some trajectory (events.py debtor_response)."""
        if not self.arises(s, (self.resp, "I1", self.quiet)):
            return self.ripe_i1(s)
        self.a4(s, "I1", lambda y: self.settle(y, "Ienforce", self.ripe_i1),
                lambda y: self.emit(y, "petition"))

    def a4(self, s: _S, phase: str, then, on_file, i3: bool = False) -> None:
        """The company's response: on the levy day before the levy (I1, post), or at the post-ruling judgment
        default's ripe date after it sought a sale or financing (ripe). A pending claim's levy-day response after the
        ruling books on its own day (events.py `waits`), so its facts come from the whole path; i3: the I3 settlement
        window is still to be asked (it precedes the levy on some trajectories), on every branch."""
        if self.first(s, (self.resp, phase, self.quiet), lambda y: self.a4(y, phase, then, on_file, i3)):
            return
        if self.pend:
            return self.a4_grouped(s, phase, then, on_file, i3)
        probe = (self.resp, phase, self.seek)
        pay = self.fc.pay_possible(self.d, s.steps, probe)
        if self.pend:  # QUESTIONS §4.4 D2: pay, initiate an offering (where one is available), file, none
            tr = self._trace(s.steps + ((self.resp, phase, self.quiet),))
            pet = np.where(tr.petition < 0, np.iinfo(np.int64).max, tr.petition)
            can = self.fc.equity and bool(((tr.day[-1] < self.N) & (tr.day[-1] < pet) & (tr.raise_offer > 0)).any())
            rest = (("initiate_offering",) if can else ()) + ("file", "none")
        else:
            rest = ("seek_sale_or_financing", "file", "neither")
        branches = (("pay",) if pay else ()) + rest
        pending = s.stayed and phase in ("I1", "post")  # legacy, unclassed route
        when = {"post": ("the creditor levies on the company's cash that day",),
                "ripe": ("the judgment default under the notes has ripened that day",),
                "entry": ("the money judgment was entered that day, unpaid; execution is stayed automatically for "
                          "its first 30 days (Fed. R. Civ. P. 62(a))",)}.get(phase, ())
        after = ("after_" + s.resp if self.pend else "after_seek") if s.a4 == "seek" else "first"
        k = self.node(self.resp, phase, s.cls, "pay" if pay else "nopay", after, *(("stay_pending",) if pending else ()),
                      s=s, probe=(self.resp, phase, self.quiet),
                      assumptions=(() if phase == "entry" else ("the judgment is enforceable, unstayed and unpaid",))
                      + when
                      + (("the company has moved for a stay, not yet approved",) if pending else ()), branches=branches)
        late = self.pend and phase in ("post", "ripe")  # booked on its own day (events.py `waits`)
        for b in branches:
            kw = {"a4": "seek" if b in self.again else "closed",
                  **({"resp": "offer" if b == "initiate_offering" else "none"} if self.pend else {})}
            y = (s.add((self.resp, phase, b), (k, b), late=s.late + ((k, len(s.steps)),), **kw) if late
                 else self.take(s, (self.resp, phase, b), (k, b), (k,), **kw))
            if b == "pay":
                paid = lambda z: self.tail(z, "paid")  # noqa: E731
                self.settle(y, "I3", paid) if i3 else paid(y)
            elif b == "file":
                self.settle(y, "I3", on_file) if i3 else on_file(y)
            elif b == "initiate_offering":  # N1 follows (QUESTIONS §4.4 N1), then the path as after 'none'
                self.offer(y, phase, then)
            else:
                then(y)

    def group_codes(self, steps) -> np.ndarray:
        """Per trajectory, the option group the last step (a probe) is asked of on the path's trajectories (-1: not
        asked, or not on the path)."""
        g = self.walk_groups(steps)
        m = self.mask_of(steps)
        return g if m is None else np.where(m, g, -1).astype(np.int8)

    def option_groups(self, steps) -> list[int]:
        """The option groups (events.Chain.option_group) among the path's trajectories where the last step (a probe)
        falls, -1 for those where it is not asked."""
        g = self.walk_groups(steps)
        m = self.mask_of(steps)
        return sorted({int(x) for x in (g if m is None else g[m])})

    def a4_grouped(self, s: _S, phase: str, then, on_file, i3: bool) -> None:
        """D2 (QUESTIONS §4.4) asked of each option group: pay where the group's cash covers the balance, initiate an
        offering where one is available, file, none. The path forks once per (group, answer), each child on its
        group's trajectories (`mask_of`) and going on as its answer requires: an offering (N1, then as after none),
        none (the response open), payment (the listing and distress chain), a filing. Trajectories where D2 is not
        asked go on as they stood (no edge)."""
        from app.analysis.events import group_tags

        probe = (self.resp, phase, self.quiet)
        codes = self.option_groups(s.steps + (probe,))
        if not any(c >= 0 for c in codes):
            return then(s)
        # Classed 14 May questions read the dated motion/denial facts. A future
        # approval answer cannot select a different earlier response question.
        pending = False
        when = {"post": ("the creditor levies on the company's cash that day",),
                "ripe": ("the judgment default under the notes has ripened that day",),
                "entry": ("the money judgment was entered that day, unpaid; execution is stayed automatically for "
                          "its first 30 days (Fed. R. Civ. P. 62(a))",)}.get(phase, ())
        after = ("after_" + s.resp) if s.a4 == "seek" else "first"
        assumed = ((() if phase == "entry" else ("the judgment is enforceable, unstayed and unpaid",)) + when
                   + (("the company has moved for a stay, not yet approved",) if pending else ()))
        late = phase in ("post", "ripe")  # booked on its own day (events.py `waits`)
        # At the ripe date the caller still owes the earlier-dated ruling chain.
        filed = on_file if phase == "ripe" else lambda z: self.tail(z, "petition")
        paid = lambda z: self.tail(z, "paid")  # noqa: E731

        def go(y: _S, b: str) -> None:
            if b == "initiate_offering":  # N1 follows (QUESTIONS §4.4 N1), then the path as after 'none'
                self.offer(y, phase, then)
            elif b == "none":
                then(y)
            elif b == "pay":
                self.settle(y, "I3", paid) if i3 else paid(y)
            else:
                self.settle(y, "I3", filed) if i3 else filed(y)

        if not self.GROUP_FORK:  # one question; each answer's path on the draws whose group offers it
            from app.analysis.events import group_branches, group_label

            g = self.group_codes(s.steps + (probe,))
            m = self.mask_of(s.steps)
            if ((g < 0) & (True if m is None else m)).any():  # not asked there: they go on as they stood
                then(s.add((self.resp, phase, f"@-1={self.quiet}"), None))
            live = sorted({int(x) for x in g[g >= 0]})
            full = tuple(b for b in ("pay", "initiate_offering", "file", "none")
                         if any(b in group_branches(self.resp, c) for c in live))
            k = self.node(self.resp, phase, s.cls, after, *(("stay_pending",) if pending else ()), s=s, probe=probe,
                          assumptions=assumed, branches=full, groups=g)
            for b in full:
                step = (self.resp, phase, group_label([c for c in live if b in group_branches(self.resp, c)], b))
                kw = {"a4": "seek" if b in self.again else "closed",
                      "resp": "offer" if b == "initiate_offering" else "none" if b == "none" else s.resp}
                go(s.add(step, (k, b), late=s.late + ((k, len(s.steps)),), **kw) if late
                   else self.take(s, step, (k, b), (k,), **kw), b)
            return
        for c in codes:
            if c < 0:  # not asked on these trajectories: they go on as they stood
                then(s.add((self.resp, phase, f"@-1={self.quiet}"), None))
                continue
            br = (("pay",) if c & 1 else ()) + (("initiate_offering",) if c & 2 else ()) + ("file", "none")
            k = self.node(self.resp, phase, s.cls, *group_tags(self.resp, c), after,
                          *(("stay_pending",) if pending else ()), s=s, probe=probe, assumptions=assumed, branches=br)
            self.fc.node_group[k] = c
            for b in br:
                step = (self.resp, phase, f"@{c}={b}")
                kw = {"a4": "seek" if b in self.again else "closed",
                      "resp": "offer" if b == "initiate_offering" else "none" if b == "none" else s.resp}
                y = (s.add(step, (k, b), late=s.late + ((k, len(s.steps)),), **kw) if late
                     else self.take(s, step, (k, b), (k,), **kw))
                if b == "initiate_offering":  # N1 follows (QUESTIONS §4.4 N1), then the path as after 'none'
                    self.offer(y, phase, then)
                elif b == "none":
                    then(y)
                elif b == "pay":
                    self.settle(y, "I3", paid) if i3 else paid(y)
                else:
                    self.settle(y, "I3", filed) if i3 else filed(y)

    def notes_petition(self, s: _S, phase: str, then) -> None:
        """The judgment default: the holders give notice and accelerate, then the issuer files, or else three holders
        file, or the notes stay due and unpaid. Paying the notes is removed by arithmetic."""
        f = self.fin
        if any(st[:2] == ("judgment_default", phase) for st in s.steps):
            return then(s)
        if f is None or not f.judgment_default_days or not self.arises(s, ("judgment_default", phase, "no")):
            return then(s)
        probe = ("judgment_default", phase, "no")
        if self.first(s, probe, lambda y: self.notes_petition(y, phase, then)):
            return
        acc = ("judgment_default", phase, "accelerated")
        issuer, holders = (acc, ("notes_due_date", "issuer", "")), (acc, ("notes_due_date", "holders", ""))
        earlier = ("entered_not_acted",) if phase != "I1" and any(x[:2] == ("judgment_default", "I1")
                                                                  for x in s.steps) else ()
        h1 = self.node("holders_act_judgment", phase, s.cls, *earlier, s=s, probe=probe)
        a5 = self.node("petition_on_notes", f"judgment_{phase}", s=s, probe=issuer,
                       assumptions=("the holders accelerate the notes",))
        # H3 only where the holders' petition, on the §3.2 route per trajectory, can fall inside the period
        inside = not self.pend or bool((self._trace(s.steps + holders).day[-1] < self.N).any())
        if inside:
            h3 = self.node("holders_involuntary", f"judgment_{phase}", s=s, probe=holders,
                           assumptions=("the notes are accelerated and unpaid", "the issuer does not file"))
            self.notes_facts(s, ((a5, issuer), (h3, holders)))
            classes = {"yes": [[(h1, "yes"), (a5, "yes")]],  # the issuer files on acceleration
                       "holders_file": [[(h1, "yes"), (a5, "no"), (h3, "yes")]],  # the holders file, per §7.06
                       "accelerated": [[(h1, "yes"), (a5, "no"), (h3, "no")]]}
        else:
            self.notes_facts(s, ((a5, issuer),))
            classes = {"yes": [[(h1, "yes"), (a5, "yes")]], "accelerated": [[(h1, "yes"), (a5, "no")]]}
        # a pending claim's default books on its own day (events.py `waits`): its facts come from each whole path
        take = ((lambda st, e, **kw: s.add(st, e, late=s.late + ((h1, len(s.steps)),), **kw)) if self.pend
                else (lambda st, e, **kw: self.take(s, st, e, (h1,), **kw)))
        if not self.pend:
            classes = self.unfiled(s, "judgment_default", phase, classes, (("holders_file", "accelerated"),))
        classes = self.joined(s, "judgment_default", phase, classes, (("yes", "holders_file"),))
        for branch, parts in classes.items():
            y = take(("judgment_default", phase, branch), (composite(parts), "yes"), notes_due=True)
            if not self.pend and branch != "accelerated" and (self._trace(y.steps, True).petition >= 0).all():
                self.floor(y, "petition")  # a petition on every trajectory
            else:
                then(y)
        then(take(probe, (composite([[(h1, "no")]]), "yes")))

    def notes_facts(self, s: _S, at) -> None:
        """The petition questions on notes due and unpaid get the facts of the day each actor may file, on the
        trajectories where the notes fell due: the issuer on the day they fall due, the holders once §7.06 allows."""
        for k, steps in at:
            self.rec(k, s.steps + steps)

    def joined(self, s: _S, node: str, ctx: str, classes: dict, sets) -> dict:
        """QUESTIONS §1 Depth at the fork: of the branches in each of `sets` (branches the walk continues the same way,
        with the same path state), those that book and read the same inside the horizon on the path's draws
        (`_same_after`) are one branch: its conjunctions are theirs together, its label the first's."""
        m = self.mask_of(s.steps)
        for names in sets:
            keep: list = []
            for c in [c for c in names if c in classes]:
                for k in keep:
                    if self._same_after(s, (node, ctx, k), (node, ctx, c), m):
                        classes[k] = classes[k] + classes.pop(c)
                        break
                else:
                    keep.append(c)
        return classes

    def _same_after(self, s: _S, a: tuple, b: tuple, m: np.ndarray | None) -> bool:
        """Whether the steps a and b after the path book and read the same inside the horizon on its draws `m`, on
        those draws alone (the path's rows, `_rows`: every trajectory's computation reads only its own row). First the
        chains one step past the shared prefix (events.Chain.divergence: what later questions read); only where they
        agree the two traces booked to the horizon: their event cash and petitions (the digest; it also catches a
        waiting step's branch, which books on its own day), petition causes and marks."""
        from app.analysis.events import BIG, canon, event_chain

        if self.d is None:  # the ordinary view (a handful of paths): on every draw
            rows_a = rows_b = None
        else:
            rows_a, rows_b = self._rows(s.steps + (a,)), self._rows(s.steps + (b,))
        chain = lambda st, r: event_chain(self.d, canon(st), self.fc.setup, self.fc.m, self.fc.draws,  # noqa: E731
                                          self.fc.sens, fin=None if self.d is not None else self.fin, rows=r)
        # The two chains compared are built from the path alone, not resumed from the prefix cache: a state served
        # from a prefix walked on more draws, then cut to these rows, books the same cash but can carry the other
        # draws' bookkeeping (zero levies, pending entries, memo keys), so a resumed comparison would depend on what
        # this process walked before, and two tasks of one group would split the same branch differently.
        cached, self.fc.draws.prefixes = self.fc.draws.prefixes, None
        try:
            ca = chain(s.steps + (a,), rows_a)
            div = ca.divergence(chain(s.steps + (b,), rows_b))
        finally:
            self.fc.draws.prefixes = cached
        on = np.ones(self.fc.draws.n, dtype=bool) if m is None else m
        if not bool(((div if len(div) != len(on) else div[on]) >= BIG).all()):  # a chain on rows is on m already
            return False
        if self.d is None:
            ta, tb = self._trace(s.steps + (a,), True), self._trace(s.steps + (b,), True)
        else:
            whole = lambda st, r: masked(self.fc.trace(self.d, st, True, rows=r, full_rows=True),  # noqa: E731
                                         self.mask_of(st))
            ta, tb = whole(s.steps + (a,), rows_a), whole(s.steps + (b,), rows_b)
        if ta.digest is None or ta.digest != tb.digest:
            return False
        if (ta.cause is None) != (tb.cause is None) or (
                ta.cause is not None and not np.array_equal(ta.cause[on], tb.cause[on])):
            return False
        ma, mb = ta.marks or {}, tb.marks or {}
        for k in set(ma) | set(mb):
            x, y = (np.broadcast_to(np.asarray(mm.get(k, BIG)), on.shape) for mm in (ma, mb))
            if not np.array_equal(np.where(x < self.N, x, BIG)[on], np.where(y < self.N, y, BIG)[on]):
                return False
        return True

    def unfiled(self, s: _S, node: str, ctx: str, classes: dict, pairs) -> dict:
        """A holders' petition that falls after the period on every trajectory books nothing: its class joins the
        class in which nobody files (the two are identical in cash, dates and state)."""
        for filed, none in pairs:
            if filed in classes and none in classes and self._trace(s.steps + ((node, ctx, filed),), True).digest \
                    == self._trace(s.steps + ((node, ctx, none),), True).digest:
                classes[none] = classes.pop(filed) + classes[none]
        return classes

    def reading(self) -> str:
        """The §7.01(i) reading held on the path (QUESTIONS §3.1; parameter judgment_default_reading)."""
        from app.analysis.events import pval

        return pval(self.fc.m, "judgment_default_reading", self.fc.sens.get("judgment_default_reading", False))

    def ripe_i1(self, s: _S) -> None:
        """The judgment as entered (QUESTIONS §3.1 base): on the day the default becomes available the company
        responds (D2), then the holders decide (H1). The response books on its own day (events.py `waits`)."""
        if self.pend and s.stayed and not any(st[:2] == ("settle", "Istay") for st in s.steps):
            return self.settle(s, "Istay", self.ripe_after_stay)
        self.ripe_after_stay(s)

    def ripe_after_stay(self, s: _S) -> None:
        after = lambda y: self.notes_petition(y, "I1", self.ruling)  # noqa: E731

        if self.pend and self.reading() == "entered" and s.a4 == "seek" \
                and not any(st[:2] == (self.resp, "ripe") for st in s.steps) \
                and self.arises(s, (self.resp, "ripe", self.quiet)):
            return self.a4(s, "ripe", after, after)
        after(s)


    # the ruling and after
    def ruling(self, s: _S) -> None:
        if self.pend:
            return self.ruling_pending(s)
        for c, parts in self.fc.ruling_classes(self.d).items():
            y = s.add(("ruling", "", c), (composite(parts), "yes"), cls=_label(c))
            if c == "none":
                self.tail(y, "vacated")
            elif c == "retrial":
                self.tail(y, "new_trial")
            else:
                self.post(y)

    def ruling_pending(self, s: _S) -> None:
        """J2 (QUESTIONS §4.1): the court leaves the money judgment unchanged, reduces it, or sets it aside (no money
        judgment on the path; any stay security released). 'Reduced' is asked only where a reduction moves the award
        across a J1b line: the surviving amount's band is the band below the award's, booked at its midpoint (the
        judgment's lowest band has none: a reduction there crosses no line). Qorvo's election on the remitted amount
        (C3) follows: accepted, the reduced judgment, with the holders asked again on the changed judgment (§3.1);
        refused, a new trial, as set aside. A ruling after the period on every trajectory is not asked."""
        probe = ("post_trial_ruling", "", "unchanged")
        if not self.arises(s, probe):
            return self.tail(s, "motions_pending")
        if self.first(s, probe, self.ruling_pending):
            return
        below = self.reduced_band(s)
        branches = ("unchanged",) + (("reduced",) if below else ()) + ("set_aside",)
        k = self.node("post_trial_ruling", s.cls, s=s, probe=probe, assumptions=("post-trial motions are pending",),
                      branches=branches)
        self.post(self.take(s, probe, (k, "unchanged"), (k,)))
        aside = [[(k, "set_aside")]]
        if below:
            lo, hi, booked = below
            # its contract `situation` lists state keys (the question state's), not marks the walker tests
            c3 = self.node("remittitur_elected", s.cls, f"remit{booked}",
                           assumptions=("the court orders a new trial unless the claimant accepts the reduced amount",))
            self.fc.remitted[c3] = (booked, lo, hi)
            step = ("post_trial_ruling", "", f"reduced:{booked}:{lo}:{hi}")
            y = self.take(s, step, (composite([[(k, "reduced"), (c3, "yes")]]), "yes"), (k, c3),
                          cls=f"reduced{booked}")
            self.notes_petition(y, "ruling", self.post)
            aside.append([(k, "reduced"), (c3, "no")])
        self.tail(self.take(s, ("post_trial_ruling", "", "set_aside"), (composite(aside), "yes"), (k,),
                            cls="set_aside"), "set_aside")

    def reduced_band(self, s: _S) -> tuple[int, int, int] | None:
        """The band below the award's on the path (J1b lines), as (low, high, booked midpoint); None where the award
        is in the lowest positive band or no award was booked by band."""
        label = next((st[2] for st in s.steps if st[0] == "verdict"), "")
        if not label.startswith("award:"):
            return None
        total = int(label.split(":")[1])
        return self.fc.reduced_band(self.d, total)

    def post(self, s: _S) -> None:
        self.settle(s, "I2", self.appeal)

    def appeal(self, s: _S) -> None:
        if s.appealed or not self.arises(s, ("appeal", "", "no")):
            return self.stay_post(s)
        if self.first(s, ("appeal", "", "no"), self.appeal):
            return
        no, yes = ("appeal", "", "no"), ("appeal", "", "yes")
        i0 = len(self.out)
        if self.pend:  # QUESTIONS §4.1 D5, §1 Depth: asked only where a later question reads the appeal
            w = _Watch({"appealed": self._raw(s.steps + (yes,), True).marks["appealed"]},
                       nodes=frozenset({"enforce_after_final", "settlement_offer", "settlement_accept"}))
            if not self._watched(s, no, w, self.stay_post):
                return  # not asked: the path books the no-appeal branch (the step, no edge)
        k = self.node("appeal", s.cls, s=s, probe=no, assumptions=("a money judgment remains outstanding",))
        if self.pend:
            self.fc.record((k,), self._facts(s.steps + (yes,)))
            self.fc.record((k,), self._facts(s.steps + (no,)))
            self._edge_after(i0, len(s.edges), (k, "no"))
            return self.appeal_settlement(s.add(yes, (k, "yes"), appealed=True))
        self.appeal_settlement(self.take(s, yes, (k, "yes"), (k,), appealed=True))
        self.stay_post(self.take(s, no, (k, "no"), (k,)))

    def appeal_settlement(self, s: _S) -> None:
        self.settle(s, "Iappeal", self.stay_post)

    def stay_post(self, s: _S) -> None:
        if s.stayed:
            return self.stayed_tail(s)
        if not self.arises(s, ("stay", "post", "no")):
            return self.i3(s)
        self._reads("unstayed")  # the stay pending appeal: asked on a path not stayed before the ruling
        probe = ("stay", "post", "no")
        if self.first(s, probe, self.stay_post):
            return
        a1 = self.node("stay_motion", "post", s.cls, s=s, probe=probe, assumptions=("the final judgment is entered",))
        j8 = self.node("stay_approved", "post", s.cls, s=s, probe=probe, assumptions=("the company moves for a stay",))
        self.stay_answers(s, "post", a1, j8,
                          lambda y: self.i3(replace(y, stayed=True), pending=True, then=self.stayed_tail),
                          lambda y: self.i3(y, pending=True), self.i3)

    def stayed_tail(self, s: _S) -> None:
        """Stayed on approval: the I4 settlement, then the notes' judgment default where it can ripen first."""
        self.settle(s, "I4", lambda z: self.notes_petition(z, "post", lambda y: self.tail(y, "stayed")))

    def i3(self, s: _S, pending: bool = False, then=None) -> None:
        """Walk an enforcement decision dated before the I3 window before settlement can terminate the claim.
        The levy and debtor response still book on their own dates; each response reaches the existing settlement
        route, whose terms read the cash on its effective date. The 4.0.0 route remains as recorded."""
        then = then or self.ripe_post
        if self.pend and self.levy_first(s):
            return self.enforce(s, then, pending=pending, i3=True)
        self.settle(s, "I3", lambda y: self.enforce(y, then, pending=pending))

    def levy_first(self, s: _S) -> bool:
        decision = self._trace(s.steps + (("enforce", "post", "none"),)).day[-1]
        window = self._trace(s.steps + (("settle", "I3", "no"),)).day[-1]
        both = (decision < self.N) & (window < self.N)
        return bool((both & (decision <= window)).any())

    def a4_post(self, s: _S, then, i3: bool = False) -> None:
        """The company's response on the day the creditor's levy falls, before the levy."""
        if s.a4 == "closed" or not self.arises(s, (self.resp, "post", self.quiet)):
            return then(s)
        self._reads("unstayed")
        self.a4(s, "post", then, lambda y: self.tail(y, "petition"), i3)

    def enforce(self, s: _S, then=None, pending: bool = False, i3: bool = False) -> None:
        """The creditor enforces (with early registration before finality). pending: the debtor has moved for a stay not yet approved; a levy counts only
        before approval, so the question is asked where it moves cash on some trajectory."""
        then = then or self.ripe_post
        levy_step, none_step = ("enforce", "post", "levy"), ("enforce", "post", "none")
        if not self.arises(s, none_step) or (pending and not self.fc.moves_cash(self.d, s.steps, levy_step, none_step)):
            return self.settle(s, 'I3', then) if i3 else then(s)
        self._reads("unstayed")
        if self.first(s, none_step, lambda y: self.enforce(y, then, pending, i3)):
            return
        extra = ("stay_pending",) if pending and not self.pend else ()
        # These identify execution routes; the 14 May renderer derives appeal status from dated question facts.
        q3 = self.node("enforce_after_final", s.cls, "appealed" if s.appealed else "final", *extra,
                       s=s, probe=none_step, assumptions=("the judgment is enforceable, unstayed and unpaid",)
                       + (("the company has moved for a stay, not yet approved",) if extra else ()))
        if s.appealed and not s.early:
            j9 = self.node("registration_early", "post", s.cls, *extra, s=s,
                           probe=("court_order", "registration_post", ""),
                           assumptions=("the creditor enforces before finality",))
            if self.pend:
                s = replace(s, late=s.late + ((j9, len(s.steps)),))
            else:
                self.court(s, j9, "registration_post")
            levy, none, keys = [[(q3, "yes"), (j9, "yes")]], [[(q3, "no")], [(q3, "yes"), (j9, "no")]], (q3,)
        else:
            levy, none, keys = [[(q3, "yes")]], [[(q3, "no")]], (q3,)
        levied = self.take(s, levy_step, (composite(levy), "yes"), keys)
        if i3:
            # The creditor decides first; the settlement decision can precede
            # the eventual levy-day response. Agreement does not release the
            # claim until its effective date, so retain intervening responses.
            self.settle(levied, 'I3', lambda y: self.a4_post(y, then),
                        lambda y: self.a4_post(y, lambda z: self.tail(z, 'settled')))
            self.settle(self.take(s, none_step, (composite(none), "yes"), keys), 'I3', then)
        else:
            self.a4_post(levied, then)
            then(self.take(s, none_step, (composite(none), "yes"), keys))

    def ripe_post(self, s: _S) -> None:
        """After 'seek a sale or financing', the company responds again at the ripe default date."""
        after = lambda y: self.notes_petition(y, "post", lambda z: self.tail(z, "unresolved"))  # noqa: E731
        if s.a4 == "seek" and not (self.pend and self.reading() == "entered") \
                and self.arises(s, (self.resp, "ripe", self.quiet)):  # the entered reading asks it in ripe_i1
            self._reads("unstayed")
            return self.a4(s, "ripe", after, lambda y: self.tail(y, "petition"))
        after(s)

    # the listing chain, then the cash floor
    def tail(self, s: _S, outcome: str) -> None:
        """The listing chain reaches collections only through the notes: once they are due and unpaid, or once a
        petition precedes it, it moves nothing and the path goes to the cash floor."""
        if self.fc.equity:
            return self.listing(s, outcome)
        f = self.fin
        probe = ("listing", "", "listed")
        if f is None or f.listing_deadline is None or not self.inside(s.steps + (probe,)):
            return self.floor(s, outcome)  # no listing chain, or an acceleration or petition precedes it
        dates = _listing_dates(self.fc, self.d)
        if min(dates["delisted_panel"], dates["delisted_suspension"]) >= self.N:
            return self.floor(s, outcome)  # delisting falls after the horizon
        if "listing_kept" in self.fc.spec and dates["panel_decision"] >= self.N:
            return self.kept(s, dates, outcome)  # listing_route: the company's hearing request alone decides
        if self.first(s, ("listing_date", "vote_call", ""), lambda y: self.tail(y, outcome)):
            return
        own = {"reverse_split_board": "vote_call", "split_approved": "effective_by", "nasdaq_hearing": "hearing_request",
               "panel_exception": "panel_decision"}  # each question at its own decision date

        def ask(name, assumptions=()):
            at = ("listing_date", own[name], "")
            k = self.node(name, s=s, probe=at, assumptions=assumptions)
            self.fc.record((k,), self._facts(s.steps + (at,)))
            return k

        a7 = ask("reverse_split_board")
        st1 = ask("split_approved", ("the board calls the vote in time",))
        a8 = ask("nasdaq_hearing", ("the stock is not compliant on the deadline",))
        n1 = ask("panel_exception", ("the issuer requests a hearing",))
        not_ok = [[(a7, "no")], [(a7, "yes"), (st1, "no")]]
        classes = {"listed": [[(a7, "yes"), (st1, "yes")]] + [c + [(a8, "yes"), (n1, "yes")] for c in not_ok],
                   "delisted_panel": [c + [(a8, "yes"), (n1, "no")] for c in not_ok],
                   "delisted_suspension": [c + [(a8, "no")] for c in not_ok]}
        for c in ("delisted_panel", "delisted_suspension"):
            if dates[c] >= self.N:  # delisted only after the horizon: listed throughout it
                classes["listed"] += classes.pop(c)
        for c, parts in classes.items():
            y = self.take(s, ("listing", "", c), (composite(parts), "yes"))
            if c == "listed":
                self.floor(y, outcome)
            else:
                self.delisting_notes(y, c, dates[c], outcome)

    def kept(self, s: _S, dates: dict, outcome: str) -> None:
        """listing_route (4.1.0): the Panel decides after the period on every trajectory, so the stock stops being
        listed inside it only by suspension without a timely hearing request: one company decision (D6)."""
        if dates["delisted_suspension"] >= self.N:
            return self.floor(s, outcome)
        at = ("listing_date", "kept", "")
        if not self.inside(s.steps + (at,)):  # a petition precedes the hearing request on every trajectory
            return self.floor(s, outcome)
        if self.first(s, at, lambda y: self.kept(y, dates, outcome)):
            return
        k = self.node("listing_kept", s=s, probe=at, assumptions=ASSUMED["listing_kept"])
        self.fc.record((k,), self._facts(s.steps + (at,)))
        self.floor(self.take(s, ("listing", "kept", "listed"), (k, "yes")), outcome)
        y = self.take(s, ("listing", "kept", "delisted_suspension"), (k, "no"))
        self.delisting_notes(y, "delisted_suspension", dates["delisted_suspension"], outcome)

    def delisting_notes(self, s: _S, dc: str, delist: int, outcome: str) -> None:
        """Delisting is an Event of Default and a Fundamental Change, where the notes are not already due. The holders
        accelerate, require the repurchase, or neither; then the issuer files, or else the holders file once §7.06
        allows, or the notes stay due and unpaid."""
        probe = ("delisting_notes", dc, "none")
        if any(st[:2] == probe[:2] for st in s.steps) or not self.inside(s.steps + (probe,)):
            return self.floor(s, outcome)  # already decided on this path, or not inside the period
        if self.first(s, probe, lambda y: self.delisting_notes(y, dc, delist, outcome)):
            return
        h2 = self.node("holders_act_delisting", dc, s=s, probe=probe, assumptions=ASSUMED["holders_act_delisting"])
        acc = ("delisting_notes", dc, "accelerated")
        issuer, holders = (acc, ("notes_due_date", "issuer", "")), (acc, ("notes_due_date", "holders", ""))
        a5 = self.node("petition_on_notes", f"delisting_{dc}", s=s, probe=issuer,
                       assumptions=ASSUMED["petition_on_notes:delisting"])
        h3 = self.node("holders_involuntary", f"delisting_{dc}", s=s, probe=holders,
                       assumptions=ASSUMED["holders_involuntary:delisting"])
        facts = [(a5, issuer), (h3, holders)]
        classes = {"petition_delist": [[(h2, "accelerate"), (a5, "yes")]],
                   "petition_delist_holders": [[(h2, "accelerate"), (a5, "no"), (h3, "yes")]],
                   "accelerated": [[(h2, "accelerate"), (a5, "no"), (h3, "no")]]}
        none = [[(h2, "neither")]]
        if self.pend:  # QUESTIONS §4.5 H2: the question asks only the declaration ('repurchase only' retires)
            pass
        elif Chain_(self.fc, self.d).repurchase_day(delist) < self.N:
            rep = ("delisting_notes", dc, "repurchase_unpaid")
            r_issuer, r_holders = (rep, ("notes_due_date", "issuer", "")), (rep, ("notes_due_date", "holders", ""))
            a5r = self.node("petition_on_notes", f"repurchase_{dc}", s=s, probe=r_issuer,
                            assumptions=ASSUMED["petition_on_notes:repurchase"])
            h3r = self.node("holders_involuntary", f"repurchase_{dc}", s=s, probe=r_holders,
                            assumptions=ASSUMED["holders_involuntary:repurchase"])
            classes["petition_repurchase"] = [[(h2, "repurchase_only"), (a5r, "yes")]]
            classes["petition_repurchase_holders"] = [[(h2, "repurchase_only"), (a5r, "no"), (h3r, "yes")]]
            classes["repurchase_unpaid"] = [[(h2, "repurchase_only"), (a5r, "no"), (h3r, "no")]]
            facts += [(a5r, r_issuer), (h3r, r_holders)]
        else:  # the repurchase date falls after the horizon: requiring it moves nothing inside it
            none.append([(h2, "repurchase_only")])
        classes["none"] = none
        self.notes_facts(s, facts)
        classes = self.unfiled(s, "delisting_notes", dc, classes, (("petition_delist_holders", "accelerated"),
                                                                   ("petition_repurchase_holders", "repurchase_unpaid")))
        classes = self.joined(s, "delisting_notes", dc, classes, (("petition_delist", "petition_delist_holders"),
                                                                  ("petition_repurchase", "petition_repurchase_holders")))
        for c, parts in classes.items():
            y = self.take(s, ("delisting_notes", dc, c), (composite(parts), "yes"), (h2,),
                          notes_due=c != "none")
            self.floor(y, outcome)

    # --- the distress chain (QUESTIONS_20240514 §4.4, §4.6): one chain for the forecast and the ordinary view ---
    def listing(self, s: _S, outcome: str, then=None, *, defer_delisting: bool = False) -> None:
        """D6a, compliance with the bid price regained by the deadline; where it is not, D6b, a timely hearing
        request (suspension stayed until the panel decides, after the period); without one, suspension and the
        delisting default (H2). Then the distress loop. Asked where no petition precedes the deadline."""
        f = self.fin
        at = ("listing_date", "compliance", "")
        if any(step[0] == 'listing' for step in s.steps):
            pending = self._pending_delisting(s)
            if pending is not None and not defer_delisting:
                return self.delisting(s, *pending, outcome, then)
            return then(s) if then is not None else self.distress(s, outcome)
        if f is None or f.listing_deadline is None or not self.inside(s.steps + (at,)):
            return then(s) if then is not None else self.distress(s, outcome)
        # Fixed listing answers establish the dated constraint that distress
        # option enumeration reads. Its own facts are deferred, so an earlier
        # distress action still appears in the listing question on those draws.
        if not (self.pend and self.d is not None) and self.first(s, at, lambda y: self.listing(y, outcome, then)):
            return
        hr = ("listing_date", "hearing_request", "")
        d6a = self.node("bid_compliance", "deadline")
        d6b = self.node("hearing_request", "determination", assumptions=("compliance is not regained by the deadline",))
        self.rec(d6a, s.steps + (at,))
        self.rec(d6b, s.steps + (hr,))
        dates = self._listing_dates()
        classes = {"compliant": [[(d6a, "yes")]], "hearing": [[(d6a, "no"), (d6b, "yes")]],
                   "suspended": [[(d6a, "no"), (d6b, "no")]]}
        to_distress = ("compliant", "hearing") + (() if dates["delisted_suspension"] < self.N else ("suspended",))
        classes = self.joined(s, "listing", "", classes, (to_distress,))
        for c, parts in classes.items():
            late = s.late + (((d6a, len(s.steps)), (d6b, len(s.steps)))
                             if self.fc.deferred_decision(d6a) else ())
            y = s.add(("listing", "", c), (composite(parts), "yes"), late=late)
            if c == "suspended" and dates["delisted_suspension"] < self.N and not defer_delisting:
                self.delisting(y, "delisted_suspension", dates["delisted_suspension"], outcome, then)
            else:
                then(y) if then is not None else self.distress(y, outcome)

    def _listing_dates(self) -> dict[str, int]:
        return _listing_dates(self.fc, self.d)

    def _repurchase_day(self, delist: int) -> int:
        return int(Chain_(self.fc, self.d).repurchase_day(np.array([delist]))[0])

    def delisting(self, s: _S, dc: str, delist: int, outcome: str, then=None) -> None:
        """The delisting default (§7.01(b)), where the notes are not already due: H2, the holders declare the notes
        due, or not (the repurchase date is in the situation); after a declaration, D9, the issuer files, or else
        H3, the holders file once §7.06 allows, or the notes stay due and unpaid. Then the distress loop."""
        probe = ("delisting_notes", dc, "none")
        done = next((st for st in s.steps if st[:2] == probe[:2]), None)
        if done is not None:  # resolved earlier on this path (a nested `_first_listing`): asked once, at its date
            out = "petition" if done[2].startswith("petition") else outcome
            return then(s) if then is not None else self.distress(s, out)
        if not self.inside(s.steps + (probe,)):
            return then(s) if then is not None else self.distress(s, outcome)
        if self.first(s, probe, lambda y: self.delisting(y, dc, delist, outcome, then)):
            return
        if self._repurchase_day(delist) < self.N:
            raise NotImplementedError("the repurchase falls due inside the period: D9 on an unpaid repurchase and H3 "
                                      "on it are not built (QUESTIONS §4.4 D9)")
        if self._declared_after(s, probe):  # QUESTIONS §1 Depth: not asked; the path books 'none' (the step, no edge)
            y = s.add(probe, None)
            return then(y) if then is not None else self.distress(y, outcome)
        acc = ("delisting_notes", dc, "accelerated")
        issuer, holders = s.steps + (acc, ("notes_due_date", "issuer", "")), s.steps + (acc, ("notes_due_date",
                                                                                              "holders", ""))
        h2 = self.node("holders_act_delisting", dc, s=s, probe=probe, assumptions=ASSUMED["holders_act_delisting"],
                       branches=("accelerate", "neither"))
        a5 = self.node("petition_on_notes", f"delisting_{dc}", s=s, probe=issuer[len(s.steps):],
                       assumptions=ASSUMED["petition_on_notes:delisting"])
        self.rec(h2, s.steps + (probe,))
        self.rec(a5, issuer)
        classes = {"petition_delist": [[(h2, "accelerate"), (a5, "yes")]],
                   "accelerated": [[(h2, "accelerate"), (a5, "no")]], "none": [[(h2, "neither")]]}
        # H3 only where the holders' petition, on the §3.2 route per trajectory, can fall inside the period (as on the
        # judgment default, `notes_petition`); otherwise it books nothing and is not asked
        if bool((self._trace(holders).day[-1] < self.N).any()):
            h3 = self.node("holders_involuntary", f"delisting_{dc}", s=s, probe=holders[len(s.steps):],
                           assumptions=ASSUMED["holders_involuntary:delisting"])
            self.rec(h3, holders)
            classes = {"petition_delist": classes["petition_delist"],
                       "petition_delist_holders": [[(h2, "accelerate"), (a5, "no"), (h3, "yes")]],
                       "accelerated": [[(h2, "accelerate"), (a5, "no"), (h3, "no")]], "none": classes["none"]}
        classes = self.joined(s, "delisting_notes", dc, classes, (("petition_delist", "petition_delist_holders"),))
        for c, parts in classes.items():
            y = s.add(("delisting_notes", dc, c), (composite(parts), "yes"), notes_due=c != "none",
                      late=s.late + (((h2, len(s.steps)),) if self.fc.deferred_decision(h2) else ()))
            then(y) if then is not None else self.distress(y, "petition" if c.startswith("petition") else outcome)

    def _pending_delisting(self, s: _S) -> tuple[str, int] | None:
        """A suspended listing leaves its later holder decision unresolved until that date is reached."""
        if ('listing', '', 'suspended') not in s.steps or any(step[0] == 'delisting_notes' for step in s.steps):
            return None
        day = self._listing_dates()['delisted_suspension']
        return ('delisted_suspension', day) if day < self.N else None

    def _first_ripe(self, s: _S, probe, then) -> bool:
        """Resolve the entered-judgment default before a later levy response or distress decision.

        The I1 levy can follow the default date. Walking its response first (and inserting distress before
        that response) would choose answer domains before an earlier offering has used the share capacity.
        """
        ripe = (self.resp, "ripe", self.quiet)
        if not self.pend or not self.fc.equity or self.reading() != "entered" or s.a4 != "seek" \
                or probe[:2] == ripe[:2] or probe[0] == "judgment_default" \
                or any(st[:2] == ripe[:2] or st[0] in ("judgment_default", "post_trial_ruling") for st in s.steps):
            return False
        a, x = self._trace(s.steps + (ripe,)), self._trace(s.steps + (probe,))
        day, at = a.day[-1], x.day[-1]
        pet = np.where(a.petition < 0, np.iinfo(np.int64).max, a.petition)
        parent = self.mask_of(s.steps)
        parent = np.ones(self.fc.draws.n, dtype=bool) if parent is None else parent
        before = parent & (day < at) & (at < self.N) & (day < pet) & (at < pet)
        if not before.any():
            return False
        after = lambda y: self.notes_petition(y, "I1", then)  # noqa: E731
        self.scoped(before, lambda: self.a4(s, "ripe", after, after))
        rest = parent & ~before
        if rest.any():
            self.scoped(rest, lambda: then(s))
        return True

    def _first_listing(self, s: _S, probe, then) -> bool:
        """Resolve only the next listing-stage decision, on draws where it precedes the parent decision."""
        if not self.fc.equity or not self.pend or self.d is None or probe[0] == 'listing_date' \
                or self.fin is None or self.fin.listing_deadline is None:
            return False
        if any(step[0] == 'listing' for step in s.steps):
            pending = self._pending_delisting(s)
            if pending is None:
                return False
            prerequisite = ('delisting_notes', pending[0], 'none')
            earlier = lambda: self.delisting(s, *pending, '', then)  # noqa: E731
        else:
            prerequisite = ('listing_date', 'compliance', '')
            # Resume the parent after D6; H2 has its own later date and is
            # reconsidered on the parent's next first() call or at the tail.
            earlier = lambda: self.listing(s, '', then, defer_delisting=True)  # noqa: E731
        listing = self._trace(s.steps + (prerequisite,))
        question = self._trace(s.steps + (probe,))
        day, at = listing.day[-1], question.day[-1]
        petition = np.where(listing.petition < 0, np.iinfo(np.int64).max, listing.petition)
        parent = self.mask_of(s.steps)
        parent = np.ones(self.fc.draws.n, dtype=bool) if parent is None else parent
        before = parent & (day < at) & (at < self.N) & (day < petition) & (at < petition)
        if not before.any():
            return False
        rest = parent & ~before
        if not rest.any():
            earlier()
        else:
            self.scoped(before, earlier)
            self.scoped(rest, lambda: then(s))
        return True

    def _declared_after(self, s: _S, probe: tuple) -> bool:
        """H2 (QUESTIONS §4.5): whether, on every trajectory of the path where the delisting default arises inside the
        horizon before any petition, a declaration would take effect (delisting + holder_notice_lag_days) only on or
        after the horizon's end. Everything it brings is dated from then (the notes due, D9's and H3's petitions, the
        coupon it replaces), so it books nothing inside the horizon, and no later question can read it."""
        from app.analysis.events import BIG, pval

        tr = self._trace(s.steps + (probe,))
        t = tr.day[-1]  # the delisting day where the default is open (BIG elsewhere)
        pet = np.where(tr.petition < 0, BIG, tr.petition)
        on = (t < self.N) & (t < pet)
        lag = int(pval(self.fc.m, "holder_notice_lag_days", self.fc.sens.get("holder_notice_lag_days", False)))
        return bool((t[on] + lag >= self.N).all())

    def _candidates(self, s: _S) -> list[tuple[str, str, str]]:
        """The next state-triggered decisions, in walk order: D7 at the next fall below the need after a recovery,
        D8 at the first unpaid obligation, §3.3 general nonpayment. Each books on its own day (events.py
        `_upto_dated`), whatever the walk order."""
        out = [("cash_floor", str(s.k), "neither")]
        if s.out == "open":
            out.append(("cash_out", "", "neither"))
        if s.np == "open" and s.out == "done":  # §3.3 needs arrears throughout its window: after the first unpaid
            out.append(("nonpayment", "", "due"))
        return out

    def distress(self, s: _S, outcome: str, then=None) -> None:
        """The distress loop: the first of the next decisions that arises inside the period before any petition is
        asked, and after each of its branches the loop again; where none arises, the path ends (or `then`)."""
        candidates, pick = self._next_distress(s)
        for j, c in enumerate(candidates):
            on = pick == j
            if on.any():
                self.scoped(on, lambda c=c: self.ask_distress(s, c, outcome, then))
        rest = pick == -1
        parent = self.mask_of(s.steps)
        if parent is not None:
            rest &= parent
        if rest.any():
            self.scoped(rest, lambda: self._end(s, outcome, then))

    def _next_distress(self, s: _S):
        """The earliest outstanding distress decision per draw, not merely the first in walk order."""
        candidates = self._candidates(s)
        best = np.full(self.fc.draws.n, self.N, dtype=np.int64)
        pick = np.full(self.fc.draws.n, -1, dtype=np.int8)
        parent = self.mask_of(s.steps)
        for j, c in enumerate(candidates):
            tr = self._trace(s.steps + (c,))
            day = tr.day[-1]
            pet = np.where(tr.petition < 0, np.iinfo(np.int64).max, tr.petition)
            on = (day < best) & (day < pet)
            if parent is not None:
                on &= parent
            best, pick = np.where(on, day, best), np.where(on, j, pick)
        return candidates, pick

    def _first_distress(self, s: _S, probe, then) -> bool:
        """Ask earlier distress only on the draws where it precedes the actor's decision.

        A future cash read is not the decision date. The complementary draws continue at the parent decision;
        promoting distress there would let a later choice change the earlier decision's available answers.
        """
        x = self._trace(s.steps + (probe,))
        dx = x.day[-1]
        parent = self.mask_of(s.steps)
        parent = np.ones(self.fc.draws.n, dtype=bool) if parent is None else parent
        candidates, pick = self._next_distress(s)
        for j, c in enumerate(candidates):
            a = self._trace(s.steps + (c,))
            t = a.day[-1]
            pet = np.where(a.petition < 0, np.iinfo(np.int64).max, a.petition)
            before = parent & (pick == j) & (t < dx) & (dx < self.N) & (t < pet) & (dx < pet)
            if not before.any():
                continue
            rest = parent & ~before
            if not rest.any():
                self.ask_distress(s, c, "", then)
            else:
                self.scoped(before, lambda c=c: self.ask_distress(s, c, "", then))
                self.scoped(rest, lambda: then(s))
            return True
        return False

    def ask_distress(self, s: _S, c: tuple, outcome: str, then) -> None:
        """Ask one distress decision (D7, D8 or §3.3); `then` continues each branch (from `first`), else the loop."""
        if self._first_listing(s, c, lambda y: self.ask_distress(y, c, outcome, then)):
            return

        def nxt(y: _S, o: str) -> None:
            then(y) if then is not None else self.distress(y, o)

        node, ctx, _ = c
        if node == "nonpayment":
            return self.nonpayment(s, c, outcome, nxt)
        from app.analysis.events import group_tags

        # asked of each option group (Owen's ruling, 29 Sep 2026): the path forks once per (group, answer)
        codes = self.option_groups(s.steps + (c,))
        if not any(x >= 0 for x in codes):
            return nxt(s, outcome)
        q, qctx = (("financing_at_floor", f"floor{ctx}") if node == "cash_floor"
                   else ("petition_cash_out", "cash_exhausted"))
        occasion, kw = (f"floor{ctx}", {"k": s.k + 1}) if node == "cash_floor" else ("cash_out", {"out": "done"})

        def go(y: _S, b: str) -> None:
            if b == "initiate_offering":  # N1, then the loop
                self.offer(y, occasion, lambda z: nxt(z, outcome))
            elif b == "neither":
                nxt(y, outcome)
            else:
                self._end(y, "petition", then)

        if not self.GROUP_FORK:  # one question; each answer's path on the draws whose group offers it
            from app.analysis.events import group_branches, group_label

            g = self.group_codes(s.steps + (c,))
            m = self.mask_of(s.steps)
            if ((g < 0) & (True if m is None else m)).any():  # not asked there (outside the horizon, after a petition)
                nxt(s.add((node, ctx, "@-1=neither"), None, **kw), outcome)
            live = sorted({int(x) for x in g[g >= 0]})
            full = tuple(b for b in ("initiate_offering", "file", "neither")
                         if any(b in group_branches(node, x) for x in live))
            k = self.node(q, qctx, s=s, probe=c, branches=full, groups=g)
            late = s.late + ((k, len(s.steps)),)
            for b in full:
                step = (node, ctx, group_label([x for x in live if b in group_branches(node, x)], b))
                go(s.add(step, (k, b), late=late, **kw), b)
            return
        for code in codes:
            if code < 0:  # not asked on these trajectories (outside the horizon or after a petition): the loop
                nxt(s.add((node, ctx, "@-1=neither"), None, **kw), outcome)
                continue
            br = (("initiate_offering",) if code & 2 else ()) + ("file", "neither")
            k = self.node(q, qctx, *group_tags(node, code), s=s, probe=c, branches=br)
            self.fc.node_group[k] = code
            late = s.late + ((k, len(s.steps)),)
            for b in br:
                y = s.add((node, ctx, f"@{code}={b}"), (k, b), late=late, **kw)
                if b == "initiate_offering":  # N1, then the loop
                    self.offer(y, occasion, lambda z: nxt(z, outcome))
                elif b == "neither":
                    nxt(y, outcome)
                else:
                    self._end(y, "petition", then)

    def offer(self, s: _S, occasion: str, then) -> None:
        """N1 after an initiation at `occasion` (D2's phase, D7's floor{k}, or cash_out): the offering closes by its
        close date on the stated terms, or does not. It books on its own day (events.py), so its facts come from each
        whole path. `then` continues each branch. Asked only where the offering can close inside the horizon on some
        trajectory of the path (QUESTIONS §1 Depth): where its close date falls on or after the horizon's end or a
        petition on every one, 'yes' books nothing either, and while it is pending no other offering can be initiated,
        so no later question reads the outcome; the path books 'no' (the step, no edge)."""
        if self._closes_after(s, occasion):
            return then(s.add(("offering", occasion, "no"), None))
        # The ordinary walk retains its existing unclassed question identities.
        prior = ("after_failed",) if self.d is None and s.failed else ()
        n1 = self.node("offering_closes", occasion, *prior, branches=("yes", "no"))
        late = s.late + ((n1, len(s.steps)),)
        for b in ("yes", "no"):
            then(s.add(("offering", occasion, b), (n1, b), late=late, failed=s.failed or b == "no"))

    def _closes_after(self, s: _S, occasion: str) -> bool:
        """Whether, on every trajectory of the path, the offering initiated at `occasion` closes (initiation +
        close_days, events.Chain.offering_terms) on or after the horizon's end or a petition."""
        from app.analysis.events import BIG

        tr = self._trace(s.steps + (("offering", occasion, "no"),), True)  # a petition before the close
        init = tr.day[-1]  # the initiation day (BIG where none was initiated)
        m = self.mask_of(s.steps)
        on = np.ones(len(init), dtype=bool) if m is None else m
        pet = np.where(tr.petition < 0, BIG, tr.petition)
        close = init + int(self.fc.m["parameters"]["offering_price"]["close_days"])
        return bool(((init < BIG) & (close >= np.minimum(self.N, pet)))[on].all())

    def nonpayment(self, s: _S, c: tuple, outcome: str, nxt) -> None:
        """§3.3: general nonpayment is met; the notes are due at once under §7.02 (no declaration). D9, the issuer
        files; else H3, the noteholders file that day (§7.06 does not bar a (j) default); else the notes stay due and
        unpaid."""
        a5 = self.node("petition_on_notes", "nonpayment", s=s, probe=c,
                       assumptions=("the notes are due and unpaid under Indenture §7.02 on the general nonpayment",))
        h3 = self.node("holders_involuntary", "nonpayment", s=s, probe=c,
                       assumptions=("the notes are due and unpaid under Indenture §7.02 on the general nonpayment",
                                    "the issuer does not file"))
        late = s.late + ((a5, len(s.steps)), (h3, len(s.steps)))
        classes = {"petition": [[(a5, "yes")]], "holders_file": [[(a5, "no"), (h3, "yes")]],
                   "due": [[(a5, "no"), (h3, "no")]]}
        classes = self.joined(s, "nonpayment", "", classes, (("petition", "holders_file"),))
        for b, parts in classes.items():
            y = s.add(("nonpayment", "", b), (composite(parts), "yes"), late=late, np="done", notes_due=True)
            nxt(y, "petition" if b != "due" else outcome)

    def first(self, s: _S, probe, then) -> bool:
        """The cash floor, and after it cash exhaustion, are state-triggered: the engine books each on its own day on
        every trajectory (events.py `upto`). A pending claim (4.1.0) asks each before the first decision it precedes on
        some trajectory, so every later-dated question's facts include it; the question's situation keeps the model's
        rule (a condition holding on only some trajectories stays unstated). 4.0.0 asks them last, as recorded.
        Returns whether it asked one; `then` continues each of its branches."""
        if self._first_ripe(s, probe, then):
            return True
        if self._first_listing(s, probe, then):
            return True
        if self.fc.equity:
            return self._first_distress(s, probe, then)
        if not self.pend or s.floor == "done":
            return False
        at = (("cash_floor", "", "continue" if self.fc.raising else "no") if s.floor == "open"
              else ("cash_out", "", "no"))
        a, x = self._trace(s.steps + (at,)), self._trace(s.steps + (probe,))
        t, dx = a.day[-1], x.day[-1]
        rx = dx if x.reads is None else np.maximum(dx, x.reads)  # a stay's approval, a settlement's payment, a levy
        pet = np.where(a.petition < 0, np.iinfo(np.int64).max, a.petition)
        if not ((t < rx) & (dx < self.N) & (t < pet) & (dx < pet)).any():
            return False
        (self.floor if s.floor == "open" else self.cash_out)(s, "", then)
        return True

    def _end(self, s: _S, outcome: str, then) -> None:
        if then is not None:
            return then(s)
        if self.fc.equity and outcome == "petition":
            for candidate in self._candidates(s):
                if self.inside(s.steps + (candidate,)):
                    return self.ask_distress(s, candidate, outcome, None)
        self.emit(s, outcome)

    def floor(self, s: _S, outcome: str, then=None) -> None:
        """The first day available cash falls below the 30-day operating need: the company files or keeps operating;
        where the case sets raise_capacity, it may also raise equity ('raise_equity' only where the amount available
        is positive on some trajectory), and zero cash stays the fallback decision. Asked last (then None: each branch
        ends the path), or early by `first` (then continues the walk; cash exhaustion is asked where it falls due)."""
        if self.fc.equity:  # QUESTIONS_20240514 §4.4: the distress loop
            return self.distress(s, outcome, then)
        if s.floor == "cash_out":
            return self.cash_out(s, outcome, then)
        if s.floor == "done":
            return self._end(s, outcome, then)
        probe = ("cash_floor", "", "no")
        if not self.inside(s.steps + (probe,)):
            return self._end(s, outcome, then)
        k = self.node("petition_cash_floor", s=s, probe=probe)
        late = self.late(s, k, probe)
        self._end(s.add(("cash_floor", "", "yes"), (k, "yes"), floor="done", late=late), "petition", then)
        y = s.add(probe, (k, "no"), floor="cash_out", late=late)
        self.cash_out(y, outcome) if then is None else then(y)

    def late(self, s: _S, k: str, probe) -> tuple:
        """A state-triggered decision's facts: a pending claim's (4.1.0) come from each whole path (it books on its own
        day, events.py `waits`); 4.0.0 books it as walked, last, so its prefix's trace has them, as recorded."""
        if self.pend:
            return s.late + ((k, len(s.steps)),)
        self.fc.record((k,), self._facts(s.steps + (probe,)))
        return s.late

    def cash_out(self, s: _S, outcome: str, then=None) -> None:
        """The first day available cash falls below zero, after the company kept operating at the floor."""
        probe = ("cash_out", "", "no")
        if not self.inside(s.steps + (probe,)):
            return self._end(s, outcome, then)  # early: it may still fall due after a later step
        k = self.node("petition_cash_out", "cash_exhausted", s=s, probe=probe)
        late = self.late(s, k, probe)
        self._end(s.add(("cash_out", "", "yes"), (k, "yes"), floor="done", late=late), "petition", then)
        self._end(s.add(probe, (k, "no"), floor="done", late=late), outcome, then)

    def terminal_trace(self, s):
        from app.analysis.events import event_trace

        return event_trace(self.d, DisputePath(instance_id=self.d.instance_id, steps=s.steps, outcome="", edges=()),
                           self.fc.setup, self.fc.m, self.fc.draws, self.fc.sens, rows=self._rows(s.steps))

    def emit(self, s: _S, outcome: str) -> None:
        m = self.mask_of(s.steps)
        tr = None
        if self.pend:  # the whole path's trace (on its trajectories): its late facts and its equivalence key
            tr = self.terminal_trace(s)
            self.keys.append(self.equivalence(s, outcome, tr, m))
            # the range of the path's cumulative event cash less encumbrance on its draws, per day (the analysis's
            # histogram bins, core.Analysis._bins, read the tree's range: pool.control merges each process's)
            cum = np.cumsum(tr.events.cash - tr.events.lock, axis=1)
            cum = cum if m is None or tr.rows is not None else cum[m]  # on the rows already (events.Trace.rows)
            if cum.size:
                r = self.fc.__dict__.setdefault("ev_range", [np.zeros(self.N), np.zeros(self.N)])
                np.minimum(r[0], cum.min(axis=0), out=r[0])
                np.maximum(r[1], cum.max(axis=0), out=r[1])
        late = self.terminal_questions(s, m, tr)
        self.out.append(DisputePath(instance_id=self.d.instance_id, steps=s.steps, outcome=outcome, edges=s.edges,
                                    mask=pack_mask(m), classes=self._classes_of(s.edges, s.steps, m, late or {})))

    def terminal_questions(self, s, m, tr):
        pending = tuple((k, i) for k, i in s.late if not self.deferred_notes(k))
        late = self.fc.record_late(self.d, s.steps, pending, m, tr=tr) if pending else {}
        if self.pend:
            from app.disputes.notes import record

            for k in sorted({k for edge, _ in s.edges for k in atoms(edge) if self.deferred_notes(k)}):
                late[k] = record(self.fc, self.d, s.steps, k, m)
        return late

    def equivalence(self, s: _S, outcome: str, tr, m: np.ndarray | None) -> tuple:
        """The path's financial-equivalence key (QUESTIONS §1 Depth; `merge_equivalent`): its draws, and on them every
        array the engine and the analysis read (the event cash by kind, encumbrance, credit capacity, incurred days,
        offering proceeds, petition day and cause, and the marks the outcome classes and offering closes read, inside
        the horizon), with its verdict class, post-trial ruling and outcome."""
        import xxhash

        from app.analysis.events import BIG

        ev, h = tr.events, xxhash.xxh3_128()
        on_rows = getattr(tr, "rows", None) is not None  # the event cash on the path's rows already (events.Trace)
        named = [("cash", ev.cash, on_rows), ("lock", ev.lock, on_rows), ("capacity", ev.capacity, on_rows),
                 ("petition", ev.petition, on_rows),
                 *((f"k:{k}", ev.kinds[k], on_rows) for k in sorted(ev.kinds)),
                 *((f"i:{k}", ev.incurred[k], on_rows) for k in sorted(ev.incurred)),
                 *((f"p:{k}", v, on_rows) for k, v in sorted((ev.proceeds or {}).items())),
                 *((("cause", tr.cause, False),) if tr.cause is not None else ()),
                 *((f"m:{k}", np.where(tr.marks[k] < self.N, tr.marks[k], BIG), False)
                   for k in ("stayed", "ruled", "paid", "settled", "raised") if tr.marks and k in tr.marks)]
        for name, a, cut in named:
            a = np.ascontiguousarray(a if m is None or cut else np.asarray(a)[m])
            h.update(name.encode() + str(a.shape).encode() + str(a.dtype).encode() + a.tobytes())
        verdict = next((x[2] for x in s.steps if x[0] == "verdict"), "")
        ruling = next((x[2] for x in s.steps if x[0] == "post_trial_ruling"), "")
        return pack_mask(m), h.digest(), verdict, ruling, outcome


class _BankWalk:
    """The bank view's chain: the company decides whether to file on the first date its available cash falls below
    its 30-day operating need, and, where it keeps operating, again on the first date its cash falls below zero."""

    def __init__(self, fc: Forecaster) -> None:
        from app.analysis.events import BANK

        self.fc, self.out, self.bank = fc, [], BANK
        self.seen: set = set()  # (node key, prefix[, record digest]) whose facts are kept

    def inside(self, steps: tuple) -> bool:
        t = self.fc.bank_trace(steps).day[-1]
        return bool((t < self.fc.days).any())

    def node(self, name: str, *ctx: str, branches: tuple[str, ...] | None = None) -> str:
        k = f"{self.bank}:{name}|" + "|".join((self.bank, *ctx))
        if k not in self.fc.bank_nodes:
            s = self.fc.spec[name]
            self.fc.bank_nodes[k] = Node(key=k, instance_id=self.bank, node=name, context="|".join((self.bank, *ctx)),
                                         cls="", question_id=s["residual_question"], event=s["decision"],
                                         assumptions=(), window=s["timing"],
                                         branches=tuple(branches or s["branches"]))
            tr = self.fc.bank_trace(self.probe[name], real=True)
            t = tr.day[-1]
            need = self.fc.draws.basis.need[np.arange(len(t)), np.clip(t, 0, self.fc.days - 1)]
            self.fc.bank_facts.setdefault(k, []).append((t, tr.cash[-1], need, tr.raise_offer))
        return k

    def emit(self, steps: tuple, edges: tuple, outcome: str) -> None:
        self.out.append(DisputePath(instance_id=self.bank, steps=steps, outcome=outcome, edges=edges))

    def run(self) -> list[DisputePath]:
        if self.fc.m["parameters"].get("ordinary_view", {}).get("value") == "same_forecast":
            return _OrdinaryWalk(self.fc).run()
        floor, out = ("cash_floor", "", "no"), ("cash_out", "", "no")
        self.probe = {"petition_cash_floor": (floor,), "petition_cash_out": (floor, out)}
        if not self.inside((floor,)):
            self.emit((), (), "operating")
            return self.out
        k = self.node("petition_cash_floor")
        self.emit(((("cash_floor", "", "yes"),)), ((k, "yes"),), "petition")
        if not self.inside((floor, out)):
            self.emit((floor,), ((k, "no"),), "operating")
            return self.out
        k2 = self.node("petition_cash_out", "cash_exhausted")
        self.emit((floor, ("cash_out", "", "yes")), ((k, "no"), (k2, "yes")), "petition")
        self.emit((floor, out), ((k, "no"), (k2, "no")), "operating")
        return self.out

class _OrdinaryWalk(_Walk):
    """Spec §16.1's ordinary view: the forecast's distress chain (`_Walk.listing` onward: the listing, the delisting
    default, D7, N1, D8, §3.3) on the ordinary engine run (events.py `bank_trace`: the dispute resolved on the review
    date at no cost). Its questions are the forecast's node types with a context free of dispute-branch tags and the
    view's situation among their assumptions; their facts go to `fc.bank_facts`. page.js `bankProbs` reads plain
    node edges only, so each path's composite edges are emitted as one path per conjunction (the same probability)."""

    GROUP_FORK = True  # the ordinary view keys its questions by group: it has no per-draw classes

    def __init__(self, fc: Forecaster) -> None:
        from app.analysis.events import BANK

        self.fc, self.out, self.bank = fc, [], BANK
        self._masks = _DepthCache(fc.SIBLINGS)
        self._watch: list[_Watch] = []
        self.d = None
        self.fin = fc.instrument()
        self.N = fc.days
        self.pend = True
        self.seen: set = set()

    def run(self) -> list[DisputePath]:
        self.listing(_S(cls=""), "operating")
        return self.out

    def _raw(self, steps, full: bool = False) -> _Prefix:
        return self.fc.bank_trace(tuple(steps), full, light=True)

    def _facts(self, steps) -> _Prefix:
        return masked(self.fc.bank_trace(tuple(steps), real=True), self.mask_of(steps))

    def _listing_dates(self) -> dict[str, int]:
        from app.analysis.events import Chain

        return Chain(None, self.fc.setup, self.fc.m, self.fc.draws, self.fc.sens, fin=self.fin).listing_dates()

    def _repurchase_day(self, delist: int) -> int:
        from app.analysis.events import Chain

        ch = Chain(None, self.fc.setup, self.fc.m, self.fc.draws, self.fc.sens, fin=self.fin)
        return int(ch.repurchase_day(np.array([delist]))[0])

    def node(self, name, *ctx, s: _S | None = None, probe=None, assumptions=(), branches=None):
        k = f"{self.bank}:{name}|" + "|".join((self.bank, *ctx))
        if k not in self.fc.bank_nodes:
            sp = self.fc.texts(name, self.fc.ordinary_dispute())  # the forecast's question: the pending block's texts
            self.fc.bank_nodes[k] = Node(key=k, instance_id=self.bank, node=name, context="|".join((self.bank, *ctx)),
                                         cls="", question_id=sp["residual_question"], event=sp["decision"],
                                         assumptions=(*assumptions, self.fc.no_cash_effect()),
                                         window=sp.get("timing", ""),
                                         branches=tuple(branches or sp["branches"]))
        return k

    def rec(self, k: str, steps) -> None:
        steps = tuple(steps)
        if (k, steps) not in self.seen:
            self.seen.add((k, steps))
            tr = self._facts(steps)
            self.row(k, tr, -1, sit=getattr(tr, "sit", None))

    def row(self, k: str, tr, i: int, late: dict | None = None, sit: dict | None = None, mask=None) -> None:
        question = (getattr(tr, "question", None) if i == -1 else getattr(tr, "questions", {}).get(i))
        if question is not None:
            question = as_of(question)
            t, cash, pet, eq, trig, sit = (question[x] for x in
                                          ("day", "cash", "petition", "raise_offer", "triggers", "sit"))
        else:
            t, cash = tr.day[i], tr.cash[i]
            pet, eq, trig = ((late["petition"], late["raise_offer"], late["triggers"]) if late
                             else (tr.petition, tr.raise_offer, tr.triggers))
        t = np.where((pet >= 0) & (pet <= t), self.fc.days, t)  # a decision after a petition is not taken
        need = self.fc.draws.basis.need[np.arange(len(t)), np.clip(t, 0, self.fc.days - 1)]
        if mask is not None:  # the path's trajectories only (a masked prefix is already: `_trace`)
            t = np.where(mask, t, self.fc.days)
        self.fc.bank_facts.setdefault(k, []).append((t, cash, need, eq, trig, sit,
                                                     None if question is None else {**question, "day": t}))

    def emit(self, s: _S, outcome: str) -> None:
        """A whole path: the facts of its state-triggered decisions on their own day (kept once per distinct
        record), then one path per combination of its composite edges' conjunctions."""
        m = self.mask_of(s.steps)
        if s.late:
            from app.analysis.events import bank_trace

            tr = bank_trace(self.fin, s.steps, self.fc.setup, self.fc.m, self.fc.draws, self.fc.sens)
            for k, i in s.late:
                late = tr.late[i]
                h = hashlib.blake2b(digest_size=16)
                if i in tr.questions:
                    h.update(pack_row(tr.questions[i]))
                for a in (tr.day[i], tr.cash[i], late["petition"], late["raise_offer"], *(() if m is None else (m,))):
                    h.update(np.ascontiguousarray(a).tobytes())
                if (k, s.steps[:i], h.digest()) not in self.seen:
                    self.seen.add((k, s.steps[:i], h.digest()))
                    self.row(k, tr, i, late, sit=tr.situations.get(i), mask=m)
        options = []
        for key, branch in s.edges:
            if key.startswith(COMPOSITE):
                if branch != "yes":
                    raise ValueError("the ordinary view expands composite edges taken on 'yes' only")
                options.append(_conjunctions(key))
            else:
                options.append((((key, branch),),))
        for combo in itertools.product(*options):
            edges = tuple(e for part in combo for e in part)
            self.out.append(DisputePath(instance_id=self.bank, steps=s.steps, outcome=outcome, edges=edges,
                                        mask=pack_mask(m)))


def bank_state(fc: Forecaster, n: Node) -> dict:
    """The bank view's question (the 20 Jun two-view design): the company, its decision, the decision dates, its
    projected available cash and 30-day operating need at the decision, the law that governs it and the notes' coupon
    (a common borrower input). It differs from the research view's question only in the research facts. Under spec
    §16.1's ordinary view it is the forecast's own question (`ordinary_state`)."""
    if fc.ordinary:
        return ordinary_state(fc, n)[0]
    s = fc.spec[n.node]
    ctx = context_phrases([c for c in n.context.split("|")[1:] if c], {})
    return {"case": {"as_of": fmt(fc.review), "analysis_period_ends": fmt(fc.horizon), "company": "the company"},
            "question": {"actor": s["actor"], "decision": s["decision"], "branches": list(n.branches),
                         "timing": s["timing"], "context": ctx},
            "standard": fc.standard(n.node), "record_items": [], "path_facts": bank_facts(fc, n), "assumptions": [],
            "evidence": [], "readings": {}}


def ordinary_state(fc: Forecaster, n: Node) -> tuple[dict, tuple[str, ...], dict]:
    """Spec §16.1: an ordinary-view question is the forecast's question of that node type, built by the same state
    builder (the case, the record items and evidence routed to it, the standard), with the ordinary view's own path
    facts, a context without the dispute's branch conditions, and the situation (the event given no cash effect)
    among the conditions that hold (the node's assumptions)."""
    rows = [r[6] if len(r) > 6 and r[6] is not None else
            {"day": r[0], "cash": r[1], "owed": np.zeros_like(r[1]), "collateral": np.zeros_like(r[1]),
             "petition": np.full_like(r[0], -1), "triggers": r[4] if len(r) > 4 else None,
             "sit": r[5] if len(r) > 5 else None} for r in fc.bank_facts.get(n.key, [])]
    return fc.built(n, fc.ordinary_dispute(), [c for c in n.context.split("|")[1:] if c], lambda: bank_facts(fc, n),
                    (rows, [r["day"] < fc.days for r in rows]))


def ordinary_classes(fc: Forecaster, n: Node) -> set[str]:
    """The situation classes (`situation_class`, as of each decision day) the ordinary view's question pools."""
    tags: set[str] = set()
    for r in fc.bank_facts.get(n.key, []):
        row = r[6] if len(r) > 6 and r[6] is not None else {
               "day": r[0], "cash": r[1], "owed": np.zeros_like(r[1]), "petition": np.full_like(r[0], -1),
               "triggers": r[4] if len(r) > 4 else None, "sit": r[5] if len(r) > 5 else None}
        c = fc.question_class(n, as_of(row), r[0] < fc.days)
        tags |= set() if c is None else {x for x in c if x}
    return tags


def bank_facts(fc: Forecaster, n: Node) -> dict:
    """The facts the bank (or ordinary) view's engine run computed for the question, pooled over its rows."""
    facts: dict = {}
    rows = fc.bank_facts.get(n.key, [])
    if rows:
        day, cash, need = (np.concatenate([r[i] for r in rows]) for i in range(3))
        eq = np.concatenate([r[3] for r in rows if len(r) > 3 and r[3] is not None]) \
            if any(len(r) > 3 and r[3] is not None for r in rows) else None
        inside = day < fc.days
        if fc.ordinary:  # spec §16.1's ordinary view: the forecast's own builder of the ordinary facts, per node type
            if n.question_id in fc.no_cash or not inside.any():
                return facts
            trig = [{"day": r[0], "triggers": r[4] or {}} for r in rows]
            return fc.ordinary_facts(n, day[inside], cash[inside], lambda: need[inside],
                                     None if eq is None else eq[inside],
                                     (trig, [r["day"] < fc.days for r in trig]), fc.instrument())
        if inside.any():  # the 20 Jun bank view (retired role-only state), as recorded
            q = lambda x, p: usd(int(np.quantile(x[inside], p)))  # noqa: E731
            facts = {"decision_date": {k: fc._date(np.quantile(day[inside], p))
                                       for k, p in (("p5", 0.05), ("p50", 0.5), ("p95", 0.95))},
                     "projected_available_cash_at_decision_date": {"p5": q(cash, 0.05), "p50": q(cash, 0.5)},
                     "operating_need_30_days_at_decision": {"p50": q(need, 0.5)}}
            if n.node == "financing_at_floor" and eq is not None:
                facts["equity_raise_available"] = fc.raise_facts(eq[inside])
            coupon = {"day": day, "triggers": {"coupon": fc.bank_trace(()).triggers["coupon"]}}
            if dates := fc._contract_dates([coupon], [inside]):  # the coupon: a common borrower input
                facts["contract_dates"] = dates
    return facts


def Chain_(fc: Forecaster, d: DisputeInstance):
    from app.analysis.events import Chain

    return Chain(d, fc.setup, fc.m, fc.draws, fc.sens)


def _listing_dates(fc: Forecaster, d: DisputeInstance) -> dict[str, int]:
    return Chain_(fc, d).listing_dates()
