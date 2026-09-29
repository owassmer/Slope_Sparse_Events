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
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Protocol

import numpy as np

from app.disputes.rules import load_model
from app.domain.investigation import AtomicFinding, DisputeInstance, SemanticObservation
from app.domain.values import usd

COMPOSITE = "="


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
    digest: bytes
    cause: np.ndarray | None = None  # per draw: the rule that booked the earliest petition (events.PETITION_CAUSES)
    marks: dict | None = None  # condition -> the day it holds from, per draw (events.MARKS)
    settle_offer: np.ndarray | None = None  # the traced step's settlement amount on its payment date (0: none)
    stay_offer: np.ndarray | None = None  # cash above the 30-day operating need on the stay-approval day (0: none)
    triggers: dict | None = None  # events.TRIGGERS name -> day index per draw (events.BIG: none)
    raise_offer: np.ndarray | None = None  # the equity available at the cash floor on the decision day (0: none)
    reads: np.ndarray | None = None  # the traced step's latest cash-read day (a payment, approval or levy day)
    sit: dict | None = None  # the traced step's question-state snapshot (events.Chain.c_situation; pending claims)
    groups: np.ndarray | None = None  # the traced step's option group per draw (events.Chain.option_group; -1: not asked)

    @classmethod
    def of(cls, tr, daily: bool = False) -> _Prefix:
        """daily: the cash by kind and the incurred days are in the digest too (the processor orders by them)."""
        from app.analysis.events import KINDS, OBLIGATIONS

        ev = tr.events
        h = hashlib.blake2b(digest_size=32)
        extra = [*(ev.kinds[k] for k in KINDS), *(ev.incurred[k] for k in OBLIGATIONS)] if daily else []
        for a in (ev.cash, ev.lock, ev.capacity, *extra):  # sparse: each non-zero's flat index and value (fixed shape)
            flat = np.ascontiguousarray(a).ravel()
            i = np.flatnonzero(flat)
            h.update(np.int64(i.size).tobytes() + i.tobytes() + flat[i].tobytes())
        h.update(np.ascontiguousarray(ev.petition).tobytes())
        last = slice(-1, None)  # later steps read only the traced step's own facts (`tr.day[-1]`)
        return cls(tr.day[last], tr.cash[last], tr.owed[last], tr.collateral[last], ev.petition.copy(), h.digest(),
                   None if tr.cause is None else tr.cause.copy(), tr.marks, tr.settle_offer, tr.stay_offer,
                   tr.triggers, getattr(tr, "raise_offer", None), getattr(tr, "reads", None),
                   (getattr(tr, "situations", None) or {}).get(len(tr.day) - 1),
                   (getattr(tr, "groups", None) or {}).get(len(tr.day) - 1))


INTERVAL_PHRASES = {"I1": "before the post-trial ruling", "I2": "after the post-trial ruling, before the appeal deadline",
                    "I3": "judgment enforceable and unstayed, after the appeal deadline",
                    "I4": "judgment stayed on approved security", "post": "after the post-trial ruling",
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
        # traced prefixes, least recently used first: the tree is walked depth-first, so only the current path's
        # prefixes and their siblings' probes are read again; each entry holds its step's question-state snapshot
        self._traces: dict = {}
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
            s = self.texts(node, d)
            self.nodes[k] = Node(key=k, instance_id=d.instance_id, node=node, context="|".join(ctx), cls="",
                                 question_id=s["residual_question"], event=s["decision"],
                                 assumptions=tuple(assumptions), window=s.get("timing", ""),  # 5.0.0: none
                                 branches=tuple(branches or s["branches"]))  # a pending claim: the block's answers
        return k

    # --- prefix traces (code timing and arithmetic, before any Jev answer) ----------------------------------------

    def trace(self, d: DisputeInstance, steps: tuple) -> _Prefix:
        """The prefix's per-step decision days and path facts, its petition days and a fingerprint of its event cash.
        The dense [draws, days] arrays are dropped once fingerprinted: the tree has thousands of prefixes, and keeping
        each prefix's arrays held about 20 GB for the Akoustis tree."""
        from app.analysis.events import event_trace

        key = (d.instance_id, steps)
        hit = self._cached(key)
        if hit is None:
            path = DisputePath(instance_id=d.instance_id, steps=steps, outcome="", edges=())
            hit = self._keep(key, _Prefix.of(event_trace(d, path, self.setup, self.m, self.draws, self.sens),
                                             self.setup.cash_processing == "daily"))
        return hit

    TRACES = 512  # the prefixes kept (the tree's depth is under 60 steps, each with a few sibling probes)

    def _cached(self, key) -> _Prefix | None:
        hit = self._traces.pop(key, None)
        if hit is not None:
            self._traces[key] = hit  # most recently used last
        return hit

    def _keep(self, key, p: _Prefix) -> _Prefix:
        if len(self._traces) >= self.TRACES:
            self._traces.pop(next(iter(self._traces)))
        self._traces[key] = p
        return p

    def situation(self, d: DisputeInstance, steps: tuple, conds: list[str]) -> tuple[set[str], set[str]]:
        """Of the conditions an actor weighs, those holding at the decision on every trajectory where it is asked, and
        those holding on none. A condition holding on some trajectories only stays unstated."""
        tr = self.trace(d, steps)
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

    def bank_trace(self, steps: tuple) -> _Prefix:
        from app.analysis.events import BANK, bank_trace

        key = (BANK, steps)
        hit = self._cached(key)
        if hit is None:
            hit = self._keep(key, _Prefix.of(bank_trace(self.instrument(), steps, self.setup, self.m, self.draws,
                                                        self.sens), self.setup.cash_processing == "daily"))
        return hit

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
                st, fids, readings = ordinary_state(self, n)
                o = await judge.forecast(n.question_id, st, (n.instance_id, *fids), n.branches)
                return Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                                event=n.event, assumptions=n.assumptions, window=n.window,
                                distribution=answer_distribution(n.key, n.branches, o), confidence=o.confidence,
                                finding_ids=fids, readings=readings, evidence=st["evidence"],
                                observation_id=o.observation_id, path_facts=st["path_facts"])
            st = bank_state(self, n)
            o = await judge.forecast(n.question_id, st, (n.instance_id,), n.branches)
            return Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                            event=n.event, assumptions=n.assumptions, window=n.window,
                            distribution=answer_distribution(n.key, n.branches, o), confidence=o.confidence,
                            observation_id=o.observation_id, path_facts=st["path_facts"])

        results = await asyncio.gather(*(one(n) for n in self.bank_nodes.values()))
        return {j.key: j for j in results}

    def moves_cash(self, d: DisputeInstance, steps: tuple, a: tuple, b: tuple) -> bool:
        """Whether two branches of a step book different event cash, encumbrance, credit capacity or petition day on
        some trajectory (else they merge)."""
        return self.trace(d, steps + (a,)).digest != self.trace(d, steps + (b,)).digest

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
            kept = dict(self.nodes), {k: list(v) for k, v in self.facts.items()}, set(self._late_seen)
            self._raise_more = set()
            out = W.run()
            more = self._raise_more - self._raise_open
            if not more:
                return out
            self._raise_open |= more
            self.nodes, self.facts, self._late_seen = kept
            W = _Walk(self, d)

    def all_paths(self) -> dict[str, dict[str, list[DisputePath]]]:
        return {d.instance_id: {"": self.paths(d)} for d, _ in self.ordered()}

    def record(self, keys, tr) -> None:
        """Keep, for each node the step asks, the facts code computed at the decision on every trajectory: its day,
        cash, amount owed and bond collateral, the petition day, and (where the chain computes them) the settlement
        offer, the reduced-security proposal and the dated contract triggers."""
        row = {"day": tr.day[-1], "cash": tr.cash[-1], "owed": tr.owed[-1], "collateral": tr.collateral[-1],
               "petition": tr.petition, "settle_offer": getattr(tr, "settle_offer", None),
               "stay_offer": getattr(tr, "stay_offer", None), "triggers": getattr(tr, "triggers", None),
               "raise_offer": getattr(tr, "raise_offer", None), "sit": getattr(tr, "sit", None),
               "marks": getattr(tr, "marks", None), "groups": getattr(tr, "groups", None)}
        for k in keys:
            self.facts.setdefault(k, []).append(row)

    def record_late(self, d: DisputeInstance, steps: tuple, late: tuple) -> None:
        """The facts of the state-triggered decisions on a whole path (the cash floor, cash exhaustion): the engine
        books each on its own day on every trajectory, after the steps dated before it wherever the walk put them
        (events.py `upto`), so its day and cash are known only once the path is. Kept once per distinct record at each
        prefix that asks it, as `record` keeps one per prefix."""
        from app.analysis.events import event_trace

        tr = event_trace(d, DisputePath(instance_id=d.instance_id, steps=steps, outcome="", edges=()), self.setup,
                         self.m, self.draws, self.sens)
        for k, i in late:
            if i in tr.stays:  # a stay's approval (daily processing): the security sized on the whole path
                self._keep_late(k, steps[:i], {**tr.stays[i], "settle_offer": None, "raise_offer": None,
                                               "sit": tr.situations.get(i), "marks": tr.marks})
                continue
            info = tr.late[i]
            n = self.nodes[k]
            if n.node == "financing_at_floor" and "noraise" in n.context.split("|"):
                pet = np.where(info["petition"] < 0, np.iinfo(np.int64).max, info["petition"])
                if ((tr.day[i] < self.days) & (tr.day[i] < pet) & (info["raise_offer"] > 0)).any():
                    self._raise_more.add(steps[:i])
            row = {"day": tr.day[i], "cash": tr.cash[i], "owed": tr.owed[i], "collateral": tr.collateral[i],
                   "petition": info["petition"], "settle_offer": None, "stay_offer": None,
                   "triggers": info["triggers"], "raise_offer": info["raise_offer"], "sit": tr.situations.get(i),
                   "marks": tr.marks, "groups": tr.groups.get(i)}
            self._keep_late(k, steps[:i], row)

    def _keep_late(self, k: str, prefix: tuple, row: dict) -> None:
        """Keep a whole path's record for a node once per distinct record at each prefix that asks it."""
        h = hashlib.blake2b(digest_size=16)
        for f in ("day", "cash", "owed", "collateral", "petition", "raise_offer", "stay_offer"):
            if row.get(f) is not None:
                h.update(f.encode() + np.ascontiguousarray(row[f]).tobytes())
        for name in sorted(row["triggers"]):
            h.update(name.encode() + np.ascontiguousarray(row["triggers"][name]).tobytes())
        seen = (k, prefix, h.digest())
        if seen not in self._late_seen:
            self._late_seen.add(seen)
            self.facts.setdefault(k, []).append(row)

    def live(self, n: Node, row: dict) -> np.ndarray:
        """The trajectories where the question's situation holds: the decision falls inside the analysis period,
        before any petition, and (for a question about an unpaid judgment) an amount is still owed."""
        day = row["day"]
        pet = np.where(row["petition"] < 0, np.iinfo(np.int64).max, row["petition"])
        ok = (day < self.days) & (day < pet)
        g = self.node_group.get(n.key)  # a question asked of one option group: its trajectories only
        if g is not None and row.get("groups") is not None:
            ok = ok & (row["groups"] == g)
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
        rows = self.facts.get(n.key, [])
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

        names = dict.fromkeys(k for r in rows for k in (r.get("triggers") or {}))
        out = {}
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
                later |= bool(((v >= self.days) & (v < BIG)).any())
            v = np.concatenate(vals) if vals else np.array([])
            if not v.size:
                continue
            lo, hi = self._date(np.quantile(v, 0.05)), self._date(np.quantile(v, 0.95))
            if "{since}" in label:  # one date: the day execution became available, 60 days before it
                label = label.format(since=f" on {self._date(v.min() - 60)}" if v.min() == v.max() else "")
            text = lo if lo == hi else f"between {lo} and {hi} (median {self._date(np.quantile(v, 0.5))})"
            out[label] = text + (", or after the analysis period ends" if later else "") + (
                self.coupon_amount() if name == "coupon" else "")
        return out

    def coupon_amount(self) -> str:
        """The coupon as the engine books it: the amount due and the part paid in cash (a common borrower input)."""
        from app.analysis.events import Chain

        fin = self.instrument()
        if fin is None or not fin.coupon_cents or self.draws is None:
            return ""
        total = fin.coupon_cents
        cash = Chain(None, self.setup, self.m, self.draws, self.sens, fin=fin).coupon_cash_cents()
        paid = ("paid in cash" if cash == total else "paid in shares" if cash == 0 else
                f"{usd(cash)} of it paid in cash and {usd(total - cash)} in shares")
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
        rows = self.facts.get(n.key, [])
        return self.built(n, d, [c for c in n.context.split("|") if c], lambda: self.path_facts(n, d),
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
                            evidence=st["evidence"] if "evidence" in st else [
                                x for k in ("historical_evidence", "party_assertions", "court_findings") for x in st[k]],
                            observation_id=o.observation_id, path_facts=st.get("path_facts", st.get("situation")))

        results = await asyncio.gather(*(one(n) for n in self.nodes.values()))
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


class _Walk:
    def __init__(self, fc: Forecaster, d: DisputeInstance) -> None:
        self.fc, self.d, self.out = fc, d, []
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

    # helpers
    def node(self, name, *ctx, s: _S | None = None, probe=None, assumptions=(), branches=None):
        """The question in its situation: the context tags given, plus the conditions its actor weighs (the model
        node's `situation`) that hold at the decision on every trajectory."""
        tags = self.situation(s, probe, name, ctx) if s is not None else ()
        return self.fc.node(self.d, name, *ctx, *tags, assumptions=assumptions, branches=branches)

    def situation(self, s: _S, probe, name: str, ctx) -> tuple[str, ...]:
        conds = list(self.fc.spec[name].get("situation", []))
        if not conds:
            return ()
        at = probe if isinstance(probe[0], tuple) else (probe,)  # one probe step, or several
        held, never = self.fc.situation(self.d, s.steps + at, conds)
        out = []
        for c in conds:
            if c == "ruled" and self.pend and s.cls in NO_JUDGMENT:
                if s.cls not in ctx:  # no money judgment on the path: that is the situation
                    out.append(s.cls)
            elif c == "ruled":
                if held & {"settled", "paid"}:  # the judgment is no longer owed: its amount is not the situation
                    continue
                if c in held and s.cls not in ctx:
                    out.append(s.cls)
                elif c in never and not any(x in INTERVAL_PHRASES for x in ctx):
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

    def _trace(self, steps) -> _Prefix:
        """The prefix's trace in this walk's view (the forecast's dispute; the ordinary view overrides it)."""
        return self.fc.trace(self.d, steps)

    def rec(self, k: str, steps) -> None:
        """Record a question's facts from the prefix `steps` (its last step is the question's own day)."""
        self.fc.record((k,), self._trace(steps))

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
            self.fc.record(keys, self.fc.trace(self.d, s.steps + (step,)))
        return s.add(step, edge, **kw)

    def court(self, s: _S, key: str, ctx: str) -> None:
        """A court's ruling on a motion gets the facts of its own day (events.py court_order): the stay's approval, with
        the security measured that day, or the registration order; the motion question keeps the motion day."""
        self.fc.record((key,), self.fc.trace(self.d, s.steps + (("court_order", ctx, ""),)))

    def stay_court(self, s: _S, key: str, ctx: str) -> _S:
        """The stay-approval question's facts. Under daily processing they come from each whole path (events.py
        `restay`: the security is sized on the approval day's balance after every event dated before it, whatever
        the walk order, and is what the engine locks); otherwise from the court's own day on the prefix (`court`)."""
        if self.fc.setup is not None and self.fc.setup.cash_processing == "daily":
            return replace(s, late=s.late + ((key, len(s.steps)),))
        self.court(s, key, ctx)
        return s

    def binary(self, s: _S, node: str, ctx: str, parts: list[list[tuple[str, str]]], keys, then_yes, then_no):
        k = composite(parts)
        then_yes(self.take(s, (node, ctx, "yes"), (k, "yes"), keys))
        then_no(self.take(s, (node, ctx, "no"), (k, "no"), keys))

    def settle(self, s: _S, interval: str, then_no) -> None:
        """A settlement exists only where its amount (cash above the 30-day need, capped at the amount owed) is
        positive: where it is zero on every trajectory the question does not arise."""
        probe = ("settle", interval, "no")
        tr = self.fc.trace(self.d, s.steps + (probe,))
        if not ((tr.day[-1] < self.N) & (tr.settle_offer > 0)).any():
            return then_no(s)
        if self.first(s, probe, lambda y: self.settle(y, interval, then_no)):
            return
        a3 = self.node("settlement_offer", interval, s.cls, s=s, probe=probe)
        from app.analysis.events import settlement_terms

        mode, count = settlement_terms(self.fc.m, self.fc.sens)
        terms = "the company offers to settle for its available cash above its 30-day operating need" + (
            f", paid in {count} equal monthly installments from the settlement date" if mode == "installments" else "")
        q4 = self.node("settlement_accept", interval, s.cls, s=s, probe=probe, assumptions=(terms,))
        self.binary(s, "settle", interval, [[(a3, "yes"), (q4, "yes")]], (a3, q4),
                    lambda y: self.tail(y, "settled"), then_no)

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
            return self.motions(s)
        self.a4(s, "entry", self.motions, lambda y: self.emit(y, "petition"))

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
        k = self.node("execute_pre_ruling", "I1", *self.cx(s), assumptions=("post-trial motions are pending",))
        self.stay_i1(self.take(s, ("execute_pre_ruling", "I1", "yes"), (k, "yes"), (k,)))
        self.ripe_i1(self.take(s, ("execute_pre_ruling", "I1", "no"), (k, "no"), (k,)))

    def stay_i1(self, s: _S) -> None:
        probe = ("stay", "I1", "no")
        if self.first(s, probe, self.stay_i1):
            return
        a1 = self.node("stay_motion", "I1", s.cls, s=s, probe=probe,
                       assumptions=("the creditor executes before the ruling",))
        j8 = self.node("stay_approved", "I1", s.cls, s=s, probe=probe, assumptions=("the company moves for a stay",))
        s = self.stay_court(s, j8, "stay_I1")
        self.binary(s, "stay", "I1", [[(a1, "yes"), (j8, "yes")]], (a1,),
                    lambda y: self.j9_stayed(replace(y, stayed=True)), self.j9_i1)

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
        k = self.node("registration_early", "I1", *self.cx(s), s=s, probe=("registration_early", "I1", "no"),
                      assumptions=("the creditor executes before finality",))
        self.court(s, k, "registration_I1")
        self.a4_i1(self.take(s, ("registration_early", "I1", "yes"), (k, "yes"), early=True))
        self.ripe_i1(self.take(s, ("registration_early", "I1", "no"), (k, "no")))

    def a4_i1(self, s: _S) -> None:
        """The debtor's response on the levy day (order + levy_lag_days), where it falls inside the horizon, before stay approval and
        before the ruling on some trajectory (events.py debtor_response)."""
        if not self.arises(s, (self.resp, "I1", self.quiet)):
            return self.ripe_i1(s)
        self.a4(s, "I1", self.ripe_i1, lambda y: self.emit(y, "petition"))

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
        pending = s.stayed and phase in ("I1", "post")  # moved for a stay, not yet approved
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

    # --- within-path grouping (Owen's ruling, 29 Sep 2026) ----------------------------------------------------------
    def option_groups(self, steps) -> list[int]:
        """The option groups (events.Chain.option_group) among the trajectories where the last step is asked."""
        tr = self._trace(steps)
        if tr.groups is None:
            return []
        t = tr.day[-1]
        pet = np.where(tr.petition < 0, np.iinfo(np.int64).max, tr.petition)
        inside = (t < self.N) & (t < pet) & (tr.groups >= 0)
        return sorted({int(x) for x in tr.groups[inside]})

    def grouped(self, keyed: list[tuple[int, str, tuple[str, ...]]]):
        """A question asked separately of each option group (keyed: (group, node key, its answers)); each trajectory
        takes its own group's answer. Yields, per combination of the groups' answers, the step's grouped branch, its
        edges (one per group) and the set of answers chosen."""
        for c, k, _ in keyed:
            self.fc.node_group[k] = c
        for choice in itertools.product(*(bs for _, _, bs in keyed)):
            branch = "@" + ";".join(f"{c}={b}" for (c, _, _), b in zip(keyed, choice, strict=True))
            yield branch, [(k, b) for (_, k, _), b in zip(keyed, choice, strict=True)], set(choice)

    def a4_grouped(self, s: _S, phase: str, then, on_file, i3: bool) -> None:
        """D2 (QUESTIONS §4.4) asked of each option group: pay where the group's cash covers the balance, initiate an
        offering where one is available, file, none. Each trajectory books its own group's answer. The path goes on as
        the most open answer requires: an offering (N1, then as after none), none, payment (the listing and distress
        chain), all file (the filing). A filed or paid trajectory is inert in what follows (no step books after a
        petition or on a resolved dispute)."""
        from app.analysis.events import group_tags

        probe = (self.resp, phase, self.quiet)
        codes = self.option_groups(s.steps + (probe,))
        if not codes:
            return then(s)
        pending = s.stayed and phase in ("I1", "post")  # moved for a stay, not yet approved
        when = {"post": ("the creditor levies on the company's cash that day",),
                "ripe": ("the judgment default under the notes has ripened that day",),
                "entry": ("the money judgment was entered that day, unpaid; execution is stayed automatically for "
                          "its first 30 days (Fed. R. Civ. P. 62(a))",)}.get(phase, ())
        after = ("after_" + s.resp) if s.a4 == "seek" else "first"
        assumed = ((() if phase == "entry" else ("the judgment is enforceable, unstayed and unpaid",)) + when
                   + (("the company has moved for a stay, not yet approved",) if pending else ()))
        keyed = []
        for c in codes:
            br = (("pay",) if c & 1 else ()) + (("initiate_offering",) if c & 2 else ()) + ("file", "none")
            k = self.node(self.resp, phase, s.cls, *group_tags(self.resp, c), after,
                          *(("stay_pending",) if pending else ()), s=s, probe=probe, assumptions=assumed, branches=br)
            keyed.append((c, k, br))
        keys = tuple(k for _, k, _ in keyed)
        late = phase in ("post", "ripe")  # booked on its own day (events.py `waits`)
        filed = lambda z: self.tail(z, "petition")  # noqa: E731  inert where every trajectory has filed
        for branch, edges, chosen in self.grouped(keyed):
            open_ = chosen & {"none", "initiate_offering"}
            resp = ("offer" if open_ == {"initiate_offering"} else "none" if open_ == {"none"} else "mixed") \
                if open_ else s.resp
            kw = {"a4": "seek" if open_ else "closed", "resp": resp}
            step = (self.resp, phase, branch)
            y = (s.add(step, edges, late=s.late + tuple((k, len(s.steps)) for k in keys), **kw) if late
                 else self.take(s, step, edges, keys, **kw))
            if "initiate_offering" in open_:  # N1 follows (QUESTIONS §4.4 N1), then the path as after 'none'
                self.offer(y, phase, then)
            elif open_:
                then(y)
            elif "pay" in chosen:
                paid = lambda z: self.tail(z, "paid")  # noqa: E731
                self.settle(y, "I3", paid) if i3 else paid(y)
            else:
                self.settle(y, "I3", filed) if i3 else filed(y)

    def notes_petition(self, s: _S, phase: str, then) -> None:
        """The judgment default: the holders give notice and accelerate, then the issuer files, or else three holders
        file, or the notes stay due and unpaid. Paying the notes is removed by arithmetic."""
        f = self.fin
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
        inside = not self.pend or bool((self.fc.trace(self.d, s.steps + holders).day[-1] < self.N).any())
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
        for branch, parts in self.unfiled(s, "judgment_default", phase, classes,
                                          (("holders_file", "accelerated"),)).items():
            y = take(("judgment_default", phase, branch), (composite(parts), "yes"), notes_due=True)
            if branch != "accelerated" and (self.fc.trace(self.d, y.steps).petition >= 0).all():
                self.floor(y, "petition")  # a petition on every trajectory
            else:
                then(y)
        then(take(probe, (composite([[(h1, "no")]]), "yes")))

    def notes_facts(self, s: _S, at) -> None:
        """The petition questions on notes due and unpaid get the facts of the day each actor may file, on the
        trajectories where the notes fell due: the issuer on the day they fall due, the holders once §7.06 allows."""
        for k, steps in at:
            self.fc.record((k,), self.fc.trace(self.d, s.steps + steps))

    def unfiled(self, s: _S, node: str, ctx: str, classes: dict, pairs) -> dict:
        """A holders' petition that falls after the period on every trajectory books nothing: its class joins the
        class in which nobody files (the two are identical in cash, dates and state)."""
        for filed, none in pairs:
            if filed in classes and none in classes and self.fc.trace(self.d, s.steps + ((node, ctx, filed),)).digest \
                    == self.fc.trace(self.d, s.steps + ((node, ctx, none),)).digest:
                classes[none] = classes.pop(filed) + classes[none]
        return classes

    def reading(self) -> str:
        """The §7.01(i) reading held on the path (QUESTIONS §3.1; parameter judgment_default_reading)."""
        from app.analysis.events import pval

        return pval(self.fc.m, "judgment_default_reading", self.fc.sens.get("judgment_default_reading", False))

    def ripe_i1(self, s: _S) -> None:
        """The judgment as entered (QUESTIONS §3.1 base): on the day the default becomes available the company
        responds (D2), then the holders decide (H1). The response books on its own day (events.py `waits`)."""
        after = lambda y: self.notes_petition(y, "I1", self.ruling)  # noqa: E731

        def filed(y: _S) -> None:  # the walk goes on where the day does not arise on some trajectory
            everywhere = (self.fc.trace(self.d, y.steps).petition >= 0).all()
            self.emit(y, "petition") if everywhere else after(y)

        if self.pend and self.reading() == "entered" and s.a4 == "seek" \
                and self.arises(s, (self.resp, "ripe", self.quiet)):
            return self.a4(s, "ripe", after, filed)
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
        bands = [b for b in self.fc.verdict_lines(self.d, self.fc.equity_inflows(self.d))["bands"] if b[1] > 0]
        at = next(i for i, (lo, hi, _) in enumerate(bands) if lo < total <= hi)
        return bands[at - 1] if at > 0 else None

    def post(self, s: _S) -> None:
        self.settle(s, "I2", self.appeal)

    def appeal(self, s: _S) -> None:
        if s.appealed or not self.arises(s, ("appeal", "", "no")):
            return self.stay_post(s)
        if self.first(s, ("appeal", "", "no"), self.appeal):
            return
        k = self.node("appeal", s.cls, s=s, probe=("appeal", "", "no"),
                      assumptions=("a money award survives the ruling",))
        self.stay_post(self.take(s, ("appeal", "", "yes"), (k, "yes"), (k,), appealed=True))
        self.stay_post(self.take(s, ("appeal", "", "no"), (k, "no"), (k,)))

    def stay_post(self, s: _S) -> None:
        if s.stayed:
            return self.stayed_tail(s)
        if not self.arises(s, ("stay", "post", "no")):
            return self.i3(s)
        probe = ("stay", "post", "no")
        if self.first(s, probe, self.stay_post):
            return
        a1 = self.node("stay_motion", "post", s.cls, s=s, probe=probe, assumptions=("the final judgment is entered",))
        j8 = self.node("stay_approved", "post", s.cls, s=s, probe=probe, assumptions=("the company moves for a stay",))
        s = self.stay_court(s, j8, "stay_post")
        self.binary(s, "stay", "post", [[(a1, "yes"), (j8, "yes")]], (a1,),
                    lambda y: self.enforce(replace(y, stayed=True), self.stayed_tail, pending=True), self.i3)

    def stayed_tail(self, s: _S) -> None:
        """Stayed on approval: the I4 settlement, then the notes' judgment default where it can ripen first."""
        self.settle(s, "I4", lambda z: self.notes_petition(z, "post", lambda y: self.tail(y, "stayed")))

    def i3(self, s: _S) -> None:
        """The I3 settlement window (after the appeal deadline), then enforcement. A pending claim (4.1.0) walks the
        enforcement first where its levy falls before that window on some trajectory (an appealed judgment registered
        early): the creditor's decision is dated at finality, before the window; the levy-day response books on its own
        day (events.py `waits`), so the settlement comes first wherever its window opens before the levy, and every
        branch of the response still reaches the window. 4.0.0 asks I3 first, as recorded."""
        if self.pend and self.levy_first(s):
            return self.enforce(s, lambda y: self.settle(y, "I3", self.ripe_post), i3=True)
        self.settle(s, "I3", self.enforce)

    def levy_first(self, s: _S) -> bool:
        levy = s.steps + (("enforce", "post", "levy"),)
        lv = self.fc.trace(self.d, levy + ((self.resp, "post", self.quiet),)).day[-1]
        w = self.fc.trace(self.d, s.steps + (("settle", "I3", "no"),)).day[-1]
        both = (lv < self.N) & (w < self.N)
        return bool((both & (lv < w)).any())

    def a4_post(self, s: _S, then, i3: bool = False) -> None:
        """The company's response on the day the creditor's levy falls, before the levy."""
        if s.a4 == "closed" or not self.arises(s, (self.resp, "post", self.quiet)):
            return then(s)
        self.a4(s, "post", then, lambda y: self.tail(y, "petition"), i3)

    def enforce(self, s: _S, then=None, pending: bool = False, i3: bool = False) -> None:
        """The creditor enforces (with early registration before finality). pending: the debtor has moved for a stay not yet approved; a levy counts only
        before approval, so the question is asked where it moves cash on some trajectory."""
        then = then or self.ripe_post
        levy_step, none_step = ("enforce", "post", "levy"), ("enforce", "post", "none")
        if not self.arises(s, none_step) or (pending and not self.fc.moves_cash(self.d, s.steps, levy_step, none_step)):
            return then(s)
        if self.first(s, none_step, lambda y: self.enforce(y, then, pending, i3)):
            return
        extra = ("stay_pending",) if pending else ()
        q3 = self.node("enforce_after_final", s.cls, "appealed" if s.appealed else "final", *extra,
                       s=s, probe=none_step, assumptions=("the judgment is enforceable, unstayed and unpaid after the ruling",)
                       + (("the company has moved for a stay, not yet approved",) if pending else ()))
        if s.appealed and not s.early:
            j9 = self.node("registration_early", "post", s.cls, *extra, s=s, probe=none_step,
                           assumptions=("the creditor enforces before finality",))
            self.court(s, j9, "registration_post")
            levy, none, keys = [[(q3, "yes"), (j9, "yes")]], [[(q3, "no")], [(q3, "yes"), (j9, "no")]], (q3,)
        else:
            levy, none, keys = [[(q3, "yes")]], [[(q3, "no")]], (q3,)
        self.a4_post(self.take(s, levy_step, (composite(levy), "yes"), keys), then, i3)
        then(self.take(s, none_step, (composite(none), "yes"), keys))

    def ripe_post(self, s: _S) -> None:
        """After 'seek a sale or financing', the company responds again at the ripe default date."""
        after = lambda y: self.notes_petition(y, "post", lambda z: self.tail(z, "unresolved"))  # noqa: E731
        if s.a4 == "seek" and not (self.pend and self.reading() == "entered") \
                and self.arises(s, (self.resp, "ripe", self.quiet)):  # the entered reading asks it in ripe_i1
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
            self.fc.record((k,), self.fc.trace(self.d, s.steps + (at,)))
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
        self.fc.record((k,), self.fc.trace(self.d, s.steps + (at,)))
        self.floor(self.take(s, ("listing", "kept", "listed"), (k, "yes")), outcome)
        y = self.take(s, ("listing", "kept", "delisted_suspension"), (k, "no"))
        self.delisting_notes(y, "delisted_suspension", dates["delisted_suspension"], outcome)

    def delisting_notes(self, s: _S, dc: str, delist: int, outcome: str) -> None:
        """Delisting is an Event of Default and a Fundamental Change, where the notes are not already due. The holders
        accelerate, require the repurchase, or neither; then the issuer files, or else the holders file once §7.06
        allows, or the notes stay due and unpaid."""
        probe = ("delisting_notes", dc, "none")
        if not self.inside(s.steps + (probe,)):
            return self.floor(s, outcome)
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
        for c, parts in classes.items():
            y = self.take(s, ("delisting_notes", dc, c), (composite(parts), "yes"), (h2,),
                          notes_due=c != "none")
            self.floor(y, outcome)

    # --- the distress chain (QUESTIONS_20240514 §4.4, §4.6): one chain for the forecast and the ordinary view ---
    def listing(self, s: _S, outcome: str) -> None:
        """D6a, compliance with the bid price regained by the deadline; where it is not, D6b, a timely hearing
        request (suspension stayed until the panel decides, after the period); without one, suspension and the
        delisting default (H2). Then the distress loop. Asked where no petition precedes the deadline."""
        f = self.fin
        at = ("listing_date", "compliance", "")
        if f is None or f.listing_deadline is None or not self.inside(s.steps + (at,)):
            return self.distress(s, outcome)
        if self.first(s, at, lambda y: self.listing(y, outcome)):
            return
        hr = ("listing_date", "hearing_request", "")
        d6a = self.node("bid_compliance", "deadline")
        d6b = self.node("hearing_request", "determination", assumptions=("compliance is not regained by the deadline",))
        self.rec(d6a, s.steps + (at,))
        self.rec(d6b, s.steps + (hr,))
        dates = self._listing_dates()
        classes = {"compliant": [[(d6a, "yes")]], "hearing": [[(d6a, "no"), (d6b, "yes")]],
                   "suspended": [[(d6a, "no"), (d6b, "no")]]}
        for c, parts in classes.items():
            y = s.add(("listing", "", c), (composite(parts), "yes"))
            if c == "suspended" and dates["delisted_suspension"] < self.N:
                self.delisting(y, "delisted_suspension", dates["delisted_suspension"], outcome)
            else:
                self.distress(y, outcome)

    def _listing_dates(self) -> dict[str, int]:
        return _listing_dates(self.fc, self.d)

    def _repurchase_day(self, delist: int) -> int:
        return int(Chain_(self.fc, self.d).repurchase_day(np.array([delist]))[0])

    def delisting(self, s: _S, dc: str, delist: int, outcome: str) -> None:
        """The delisting default (§7.01(b)), where the notes are not already due: H2, the holders declare the notes
        due, or not (the repurchase date is in the situation); after a declaration, D9, the issuer files, or else
        H3, the holders file once §7.06 allows, or the notes stay due and unpaid. Then the distress loop."""
        probe = ("delisting_notes", dc, "none")
        if not self.inside(s.steps + (probe,)):
            return self.distress(s, outcome)
        if self.first(s, probe, lambda y: self.delisting(y, dc, delist, outcome)):
            return
        if self._repurchase_day(delist) < self.N:
            raise NotImplementedError("the repurchase falls due inside the period: D9 on an unpaid repurchase and H3 "
                                      "on it are not built (QUESTIONS §4.4 D9)")
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
        for c, parts in classes.items():
            self.distress(s.add(("delisting_notes", dc, c), (composite(parts), "yes"), notes_due=c != "none"),
                          "petition" if c.startswith("petition") else outcome)

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
        for c in self._candidates(s):
            if self.inside(s.steps + (c,)):
                return self.ask_distress(s, c, outcome, then)
        return self._end(s, outcome, then)

    def _first_distress(self, s: _S, probe, then) -> bool:
        """`first` under the equity model: a distress decision dated before the probe's decision (or the latest day
        whose cash it reads) on some trajectory, before any petition, is asked first, so the probe's facts include
        it; `then` continues each of its branches."""
        x = self._trace(s.steps + (probe,))
        dx = x.day[-1]
        rx = dx if x.reads is None else np.maximum(dx, x.reads)
        for c in self._candidates(s):
            a = self._trace(s.steps + (c,))
            t = a.day[-1]
            pet = np.where(a.petition < 0, np.iinfo(np.int64).max, a.petition)
            if ((t < rx) & (dx < self.N) & (t < pet) & (dx < pet)).any():
                self.ask_distress(s, c, "", then)
                return True
        return False

    def ask_distress(self, s: _S, c: tuple, outcome: str, then) -> None:
        """Ask one distress decision (D7, D8 or §3.3); `then` continues each branch (from `first`), else the loop."""
        def nxt(y: _S, o: str) -> None:
            then(y) if then is not None else self.distress(y, o)

        node, ctx, _ = c
        if node == "nonpayment":
            return self.nonpayment(s, c, outcome, nxt)
        from app.analysis.events import group_tags

        codes = self.option_groups(s.steps + (c,))  # asked of each option group (Owen's ruling, 29 Sep 2026)
        if not codes:
            return nxt(s, outcome)
        q, qctx = (("financing_at_floor", f"floor{ctx}") if node == "cash_floor"
                   else ("petition_cash_out", "cash_exhausted"))
        occasion, kw = (f"floor{ctx}", {"k": s.k + 1}) if node == "cash_floor" else ("cash_out", {"out": "done"})
        keyed = []
        for code in codes:
            br = (("initiate_offering",) if code & 2 else ()) + ("file", "neither")
            keyed.append((code, self.node(q, qctx, *group_tags(node, code), s=s, probe=c, branches=br), br))
        late = s.late + tuple((k, len(s.steps)) for _, k, _ in keyed)
        for branch, edges, chosen in self.grouped(keyed):
            y = s.add((node, ctx, branch), edges, late=late, **kw)
            if "initiate_offering" in chosen:  # N1 on the trajectories that initiated, then the loop
                self.offer(y, occasion, lambda z: nxt(z, outcome))
            elif "neither" in chosen:
                nxt(y, outcome)
            else:  # every group files
                self._end(y, "petition", then)

    def offer(self, s: _S, occasion: str, then) -> None:
        """N1 after an initiation at `occasion` (D2's phase, D7's floor{k}, or cash_out): the offering closes by its
        close date on the stated terms, or does not. It books on its own day (events.py), so its facts come from each
        whole path. `then` continues each branch."""
        n1 = self.node("offering_closes", occasion, *(("after_failed",) if s.failed else ()), branches=("yes", "no"))
        late = s.late + ((n1, len(s.steps)),)
        for b in ("yes", "no"):
            then(s.add(("offering", occasion, b), (n1, b), late=late, failed=s.failed or b == "no"))

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
        for b, parts in classes.items():
            y = s.add(("nonpayment", "", b), (composite(parts), "yes"), late=late, np="done", notes_due=True)
            nxt(y, "petition" if b != "due" else outcome)

    def first(self, s: _S, probe, then) -> bool:
        """The cash floor, and after it cash exhaustion, are state-triggered: the engine books each on its own day on
        every trajectory (events.py `upto`). A pending claim (4.1.0) asks each before the first decision it precedes on
        some trajectory, so every later-dated question's facts include it; the question's situation keeps the model's
        rule (a condition holding on only some trajectories stays unstated). 4.0.0 asks them last, as recorded.
        Returns whether it asked one; `then` continues each of its branches."""
        if self.fc.equity:
            return self._first_distress(s, probe, then)
        if not self.pend or s.floor == "done":
            return False
        at = (("cash_floor", "", "continue" if self.fc.raising else "no") if s.floor == "open"
              else ("cash_out", "", "no"))
        a, x = self.fc.trace(self.d, s.steps + (at,)), self.fc.trace(self.d, s.steps + (probe,))
        t, dx = a.day[-1], x.day[-1]
        rx = dx if x.reads is None else np.maximum(dx, x.reads)  # a stay's approval, a settlement's payment, a levy
        pet = np.where(a.petition < 0, np.iinfo(np.int64).max, a.petition)
        if not ((t < rx) & (dx < self.N) & (t < pet) & (dx < pet)).any():
            return False
        (self.floor if s.floor == "open" else self.cash_out)(s, "", then)
        return True

    def _end(self, s: _S, outcome: str, then) -> None:
        self.emit(s, outcome) if then is None else then(s)

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
        self.fc.record((k,), self.fc.trace(self.d, s.steps + (probe,)))
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

    def emit(self, s: _S, outcome: str) -> None:
        if s.late:
            self.fc.record_late(self.d, s.steps, s.late)
        self.out.append(DisputePath(instance_id=self.d.instance_id, steps=s.steps, outcome=outcome, edges=s.edges))


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
            tr = self.fc.bank_trace(self.probe[name])
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

    def __init__(self, fc: Forecaster) -> None:
        from app.analysis.events import BANK

        self.fc, self.out, self.bank = fc, [], BANK
        self.d = None
        self.fin = fc.instrument()
        self.N = fc.days
        self.pend = True
        self.seen: set = set()

    def run(self) -> list[DisputePath]:
        self.listing(_S(cls=""), "operating")
        return self.out

    def _trace(self, steps) -> _Prefix:
        return self.fc.bank_trace(tuple(steps))

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
            tr = self._trace(steps)
            self.row(k, tr, -1, sit=getattr(tr, "sit", None), groups=getattr(tr, "groups", None))

    def row(self, k: str, tr, i: int, late: dict | None = None, sit: dict | None = None, groups=None) -> None:
        t = tr.day[i]
        pet, eq, trig = ((late["petition"], late["raise_offer"], late["triggers"]) if late
                         else (tr.petition, tr.raise_offer, tr.triggers))
        t = np.where((pet >= 0) & (pet <= t), self.fc.days, t)  # a decision after a petition is not taken
        need = self.fc.draws.basis.need[np.arange(len(t)), np.clip(t, 0, self.fc.days - 1)]
        g = self.fc.node_group.get(k)  # a grouped question: its option group's trajectories only
        if g is not None and groups is not None:
            t = np.where(groups == g, t, self.fc.days)
        self.fc.bank_facts.setdefault(k, []).append((t, tr.cash[i], need, eq, trig, sit))  # sit: the snapshot

    def emit(self, s: _S, outcome: str) -> None:
        """A whole path: the facts of its state-triggered decisions on their own day (kept once per distinct
        record), then one path per combination of its composite edges' conjunctions."""
        if s.late:
            from app.analysis.events import bank_trace

            tr = bank_trace(self.fin, s.steps, self.fc.setup, self.fc.m, self.fc.draws, self.fc.sens)
            for k, i in s.late:
                late = tr.late[i]
                h = hashlib.blake2b(digest_size=16)
                for a in (tr.day[i], tr.cash[i], late["petition"], late["raise_offer"]):
                    h.update(np.ascontiguousarray(a).tobytes())
                if (k, s.steps[:i], h.digest()) not in self.seen:
                    self.seen.add((k, s.steps[:i], h.digest()))
                    self.row(k, tr, i, late, sit=tr.situations.get(i), groups=tr.groups.get(i))
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
            self.out.append(DisputePath(instance_id=self.bank, steps=s.steps, outcome=outcome, edges=edges))


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
    rows = [{"day": r[0], "cash": r[1], "owed": np.zeros_like(r[1]), "collateral": np.zeros_like(r[1]),
             "petition": np.full_like(r[0], -1), "triggers": r[4] if len(r) > 4 else None,
             "sit": r[5] if len(r) > 5 else None} for r in fc.bank_facts.get(n.key, [])]
    return fc.built(n, fc.ordinary_dispute(), [c for c in n.context.split("|")[1:] if c], lambda: bank_facts(fc, n),
                    (rows, [r["day"] < fc.days for r in rows]))


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
