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

import functools
import itertools
import json
import math
import pickle
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import numpy as np
from zlib_ng import zlib_ng

from app.disputes.rules import load_model
from app.domain.investigation import AtomicFinding, DisputeInstance, SemanticObservation
from app.domain.values import usd

COMPOSITE = "="
CLASS_TAG = "#"  # a question's situation class in its key (`situation_class`)
ROWS_OFF = __import__("os").environ.get("SLOPE_ROWS") == "0"  # `_Walk._rows`: every trace on every draw


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
    # the answer-first walk (app/disputes/answer_first.py): the probability with which the path was sampled times its
    # stub's groups and replicates per group (1: walked exactly), so its weight on a draw is its probability divided
    # by this; the stub (set-aside point) and group it was sampled in (-1: walked exactly)
    divisor: float = 1.0
    stub: int = -1
    group: int = -1


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
# not happened there, and a date counted from one of them is not yet fixed: each trigger carries the day its date is
# fixed (events.KNOWN), and before it the date is removed. An outcome dated after the decision is removed.
OUTCOME_DAYS = ("delisted", "stayed_from", "notes_due_day", "nonpayment_day", "first_unpaid")


def as_of(row: dict) -> dict:
    """The row as of its decision day on each trajectory (`OUTCOME_DAYS`): an outcome dated after the decision day
    reads as not having happened (BIG; a petition -1; the marks BIG), an offering initiated after it is not there
    and one closing after it has not closed, and a trigger that exists only because of such an outcome is absent."""
    from app.analysis.events import BIG, KNOWN

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
        t2 = {k: v for k, v in trig.items() if not k.startswith(KNOWN)}
        for name, v in t2.items():
            if name == "coupon_cash" or not per(v) or not (v < BIG).any():
                continue
            fixed = trig.get(KNOWN + name)
            if not per(fixed):
                raise ValueError(f"the trigger {name} without the day its date is fixed: whether it is known cannot "
                                 "be told")
            t2[name] = np.where(later(fixed), BIG, v).astype(v.dtype)
        if per(sit.get("motions_filed")) and "appeal_deadline" in t2:
            # a pending claim: from entry, the deadline as entry sets it; once motions are filed, none until the
            # order disposing of them, then the deadline that order fixes
            filed = sit["motions_filed"] <= day
            ruled = sit["ruling"] <= day
            t2["appeal_deadline"] = np.where(
                filed, np.where(ruled, trig["appeal_deadline"], BIG), sit["entry_appeal_deadline"])
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
        self.ruling_bands: dict[str, list] = {}  # J2 node key -> the bands below the award (booked, low, high)
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


    OPEN_LIGHT = 16  # light day-only traces kept completable (each holds its finished chain)


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


    # --- the bank view: the bank data and the common borrower inputs ----------------------------------------------

    def instrument(self):
        """The borrower's instrument whose terms are a common input to both views (the notes' coupon), or None."""
        return next((f for d in self.disputes for f in d.financing if f.status != "superseded"), None)


    # --- the post-trial ruling: merits nodes and the chain rule --------------------------------------------------


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


    CLASSES = __import__("os").environ.get("SLOPE_CLASSES") != "0"  # 0: every question pools its situations


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

    def _fixed_amount(self, d: DisputeInstance, f) -> str:
        """Where the record reads the amount as fixed by the court: the judgment's amount and date, as a fact."""
        from app.analysis.events import entered_cents

        levels = self.m["factors"].get(f.factor_id, {}).get("levels", [])
        dist = f.distribution or {}
        if not (levels and dist and d.judgment_date) or max(dist, key=dist.get) != levels[-1]:
            return ""
        return f"{usd(entered_cents(d))}, in the judgment entered {fmt(d.judgment_date)}"

    def _source(self, fid: str) -> str:
        """The title of the source a finding's passage comes from."""
        if fid not in self._sources:
            f = self.findings.get(fid)
            self._sources[fid] = self.hydrate(f).get("source", "") if f is not None else ""
        return self._sources[fid]


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
        return self.built(n, d, tags, None, (rows, [self.live(n, r) for r in rows]))

    def built(self, n: Node, d: DisputeInstance, tags: list[str], facts, rows: tuple = ([], [])
              ) -> tuple[dict, tuple[str, ...], dict]:
        """A question's state: the case, the decision in its situation (`tags`), the standard, the record items and
        evidence routed to it, the path facts (`facts()`), and the conditions that hold. The forecast and the
        ordinary view (`ordinary_state`) both build their questions here. A 14 May question (profile
        event_forecast) is built from its rows (rows, live masks) by app/disputes/state14.py."""
        if not self.event_forecast(n):
            raise NotImplementedError(f"{n.question_id}: only the 14 May questions (profile event_forecast) are built")
        from app.disputes import state14

        return state14.build(self, n, d, tags, *rows, strict=getattr(self, "state_strict", True))


def _label(c: str) -> str:
    return c.split(":")[0] + (c.split(":")[1] if c.startswith("amt:") else "") + (
        "_retrial" if c.endswith(":retrial") else "")


def Chain_(fc: Forecaster, d: DisputeInstance):
    from app.analysis.events import Chain

    return Chain(d, fc.setup, fc.m, fc.draws, fc.sens)


def _listing_dates(fc: Forecaster, d: DisputeInstance) -> dict[str, int]:
    return Chain_(fc, d).listing_dates()
