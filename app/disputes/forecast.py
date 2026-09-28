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
    it ([draws] each); the petition day per draw; and a fingerprint of the event cash, encumbrance and credit
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

    @classmethod
    def of(cls, tr) -> _Prefix:
        ev = tr.events
        h = hashlib.blake2b(digest_size=32)
        for a in (ev.cash, ev.lock, ev.capacity):  # sparse: each non-zero's flat index and value (fixed shape)
            flat = np.ascontiguousarray(a).ravel()
            i = np.flatnonzero(flat)
            h.update(np.int64(i.size).tobytes() + i.tobytes() + flat[i].tobytes())
        h.update(np.ascontiguousarray(ev.petition).tobytes())
        return cls(tr.day, tr.cash, tr.owed, tr.collateral, ev.petition.copy(), h.digest(),
                   None if tr.cause is None else tr.cause.copy(), tr.marks, tr.settle_offer, tr.stay_offer,
                   tr.triggers, getattr(tr, "raise_offer", None))


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
VERDICT_NODES = ("verdict_finding", "verdict_measure")  # the jury's verdict-form questions (4.1.0 verdict_form)
MERITS = ("ts_liability_jmol", "ts_damages_ruling", "remittitur_accepted", "patent_jmol", "trebling", "fees_awarded",
          "prejudgment_interest", "injunction")
# questions about an unpaid judgment: their facts pool only trajectories where an amount is still owed
OWED = {"execute_pre_ruling", "stay_motion", "stay_approved", "registration_early", "debtor_response", "judgment_response",
        "enforce_after_final", "settlement_offer", "settlement_accept", "holders_act_judgment"}
# questions whose actor weighs the contract dates ahead: settlement, the cash floor, cash running out, the notes
DATED = {"settlement_offer", "settlement_accept", "petition_cash_floor", "financing_at_floor", "petition_cash_out",
         "holders_act_judgment", "judgment_response", "listing_kept",
         "holders_act_delisting", "holders_involuntary", "petition_on_notes"}
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
        self._traces: dict = {}
        self._sources: dict[str, str] = {}  # finding -> its source's title
        self.bank_nodes: dict[str, Node] = {}  # the bank view's questions (bank_state)
        self.bank_facts: dict[str, list] = {}  # bank node key -> [(day, cash, need, raise offer) arrays]
        # the case sets raise_capacity: the company's floor decision is financing_at_floor (4.1.0)
        self.raising = "value" in self.m["parameters"].get("raise_capacity", {})

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
            s = self.spec[node]
            self.nodes[k] = Node(key=k, instance_id=d.instance_id, node=node, context="|".join(ctx), cls="",
                                 question_id=s["residual_question"], event=s["decision"],
                                 assumptions=tuple(assumptions), window=s["timing"],
                                 branches=tuple(branches or s["branches"]))
        return k

    # --- prefix traces (code timing and arithmetic, before any Jev answer) ----------------------------------------

    def trace(self, d: DisputeInstance, steps: tuple) -> _Prefix:
        """The prefix's per-step decision days and path facts, its petition days and a fingerprint of its event cash.
        The dense [draws, days] arrays are dropped once fingerprinted: the tree has thousands of prefixes, and keeping
        each prefix's arrays held about 20 GB for the Akoustis tree."""
        from app.analysis.events import event_trace

        key = (d.instance_id, steps)
        if key not in self._traces:
            path = DisputePath(instance_id=d.instance_id, steps=steps, outcome="", edges=())
            self._traces[key] = _Prefix.of(event_trace(d, path, self.setup, self.m, self.draws, self.sens))
        return self._traces[key]

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
        if key not in self._traces:
            self._traces[key] = _Prefix.of(bank_trace(self.instrument(), steps, self.setup, self.m, self.draws,
                                                      self.sens))
        return self._traces[key]

    def bank_paths(self) -> list[DisputePath]:
        """The bank view's paths (none without the operating draws)."""
        return _BankWalk(self).run() if self.draws is not None else []

    async def judge_bank(self, judge: ForecastJudge) -> dict[str, Judgment]:
        async def one(n: Node) -> Judgment:
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

    def verdict_classes(self, d: DisputeInstance) -> dict[str, list[list[tuple[str, str]]]]:
        """Each verdict branch's disjoint conjunctions of the jury's answers on the case's verdict form (template
        verdict_form), in the form's order: a question is one node per sequence of earlier answers that reaches it,
        and each answer leads to the next question or to a verdict branch. The branches' composites are exhaustive
        over the answers (every sequence ends in exactly one branch)."""
        form = self.m["case_verdict_form"]
        branches = self.m["templates"]["pending_money_claim"]["verdict_branches"]
        out: dict[str, list] = {b: [] for b in branches}

        def walk(q: str, trail: tuple[str, ...], conj: list) -> None:
            spec = form["questions"][q]
            k = self.node(d, spec["node"], q, *trail)
            for ans in ("yes", "no"):
                nxt, c = spec[ans], conj + [(k, ans)]
                if nxt in branches:
                    out[nxt].append(c)
                else:
                    walk(nxt, trail + (f"{q}={ans}",), c)

        walk(form["start"], (), [])
        return {b: parts for b, parts in out.items() if parts}

    def labels(self, d: DisputeInstance) -> dict[str, str]:
        """A pending claim's label templates filled from case inputs (never a party name in the contract); {} for 4.0.0
        disputes. The claimant's branch is told its range over the enhancement settings, never one figure."""
        if d.stage != PENDING:
            return {}
        from app.analysis.events import verdict_amount

        lo, hi = (verdict_amount(d, self.m, "claimant_theory", {"claimant_enhancements": x}) for x in (False, True))
        fill = {"claimant": d.counterparty, "range": f"{usd(min(lo, hi))} to {usd(max(lo, hi))}",
                "amount": usd(verdict_amount(d, self.m, "without_principal_measure", self.sens)),
                **self.m.get("case_labels", {})}
        return {k: v.format_map(fill) for k, v in self.m["templates"]["pending_money_claim"]["label_templates"].items()}

    def verdict_context(self, n: Node) -> dict:
        """A verdict-form question as the jury meets it: the question quoted, what is asked of it, and its earlier
        answers on the form."""
        form = self.m["case_verdict_form"]
        tags = [c for c in n.context.split("|") if c]
        q = form["questions"][tags[0]]
        earlier = [f"{form['questions'][x.split('=')[0]]['form']}: {'Yes' if x.endswith('=yes') else 'No'}"
                   for x in tags[1:]]
        return {"verdict_form": form["source"], "form_question": f"{q['form']}: \u201c{q['quote']}\u201d",
                **({"asked": q["asks"]} if "asks" in q else {}), "earlier_answers": earlier}


    # --- the chain walk ---------------------------------------------------------------------------------------------

    def paths(self, d: DisputeInstance) -> list[DisputePath]:
        """Every structurally feasible path through the dispute's chains (see the module docstring)."""
        if d.borrower_role != "debtor" or d.stage not in ("post_trial", "judgment_entered", "enforcement",
                                                           "appeal_filed", "appeal_pending", PENDING):
            return [DisputePath(instance_id=d.instance_id, steps=(), outcome="outside_chains", edges=())]
        W = _Walk(self, d)
        return W.run()

    def all_paths(self) -> dict[str, dict[str, list[DisputePath]]]:
        return {d.instance_id: {"": self.paths(d)} for d, _ in self.ordered()}

    def record(self, keys, tr) -> None:
        """Keep, for each node the step asks, the facts code computed at the decision on every trajectory: its day,
        cash, amount owed and bond collateral, the petition day, and (where the chain computes them) the settlement
        offer, the reduced-security proposal and the dated contract triggers."""
        row = {"day": tr.day[-1], "cash": tr.cash[-1], "owed": tr.owed[-1], "collateral": tr.collateral[-1],
               "petition": tr.petition, "settle_offer": getattr(tr, "settle_offer", None),
               "stay_offer": getattr(tr, "stay_offer", None), "triggers": getattr(tr, "triggers", None),
               "raise_offer": getattr(tr, "raise_offer", None)}
        for k in keys:
            self.facts.setdefault(k, []).append(row)

    def live(self, n: Node, row: dict) -> np.ndarray:
        """The trajectories where the question's situation holds: the decision falls inside the analysis period,
        before any petition, and (for a question about an unpaid judgment) an amount is still owed."""
        day = row["day"]
        pet = np.where(row["petition"] < 0, np.iinfo(np.int64).max, row["petition"])
        ok = (day < self.days) & (day < pet)
        return ok & (row["owed"] > 0) if n.node in OWED else ok


    # --- the residual questions' state and Jev --------------------------------------------------------------------

    def _date(self, t: float) -> str:
        return fmt(self.review + timedelta(days=int(t) + 1))

    def path_facts(self, n: Node, d: DisputeInstance) -> dict:
        """What code computed for this node, pooled over the paths that reach it, before any Jev answer."""
        reg = registry_entry(n.question_id)
        remit = self.m["remittitur_scenarios"]["scenarios"].get("remitted", {})
        premise = n.node in ("ts_damages_ruling", "remittitur_accepted") or bool(
            set(n.context.split("|")) & self.remit_classes)
        facts: dict = {"components": [self._component(c, remit if premise else {}) for c in d.components]}
        if n.node in MERITS:
            facts["pending_motions"] = self._pending(d, n.node)
        if n.question_id in self.no_cash:
            return facts
        rows = self.facts.get(n.key, [])
        masks = [self.live(n, r) for r in rows]
        if not any(m.any() for m in masks):
            return facts
        day, cash, owed = (np.concatenate([r[f][m] for r, m in zip(rows, masks, strict=True)])
                           for f in ("day", "cash", "owed"))
        facts["decision_date"] = {"p5": self._date(np.quantile(day, 0.05)), "p50": self._date(np.quantile(day, 0.5)),
                                  "p95": self._date(np.quantile(day, 0.95))}
        facts["projected_available_cash_at_decision_date"] = {"p5": usd(int(np.quantile(cash, 0.05))),
                                                              "p50": usd(int(np.quantile(cash, 0.5)))}
        facts["amount_owed_at_decision"] = {"p50": usd(int(np.quantile(owed, 0.5))), "max": usd(int(owed.max()))}
        if n.node in ("petition_cash_floor", "financing_at_floor", "petition_cash_out") and self.draws is not None:
            need = np.concatenate([self.draws.basis.need[np.arange(len(r["day"])), np.clip(r["day"], 0, self.days - 1)][m]
                                   for r, m in zip(rows, masks, strict=True)])
            facts["operating_need_30_days_at_decision"] = {"p50": usd(int(np.quantile(need, 0.5)))}
        merged = next((self.class_range[c] for c in n.context.split("|") if c in self.class_range), None)
        if merged is not None:  # a merged class: the range of its judgment amounts, beside the amount owed
            facts["judgment_after_ruling"] = f"{usd(merged[0])} to {usd(merged[1])}"
            facts["amount_owed_at_decision"]["basis"] = ("the lowest judgment in that range, with post-judgment "
                                                         "interest, less any amount collected")
        if n.node == "financing_at_floor" and (eq := self._pooled(rows, masks, "raise_offer")) is not None:
            facts["equity_raise_available"] = self.raise_facts(eq)
        if reg.get("node") in ("stay_motion", "stay_approved"):
            facts["bond_collateral_required"] = usd(int(np.quantile(self._collateral(d, owed), 0.5)))
        if n.node == "stay_approved" and (offer := self._pooled(rows, masks, "stay_offer")) is not None:
            facts["reduced_security_offered"] = {"p5": usd(int(np.quantile(offer, 0.05))),
                                                 "p50": usd(int(np.quantile(offer, 0.5)))}
        if n.node in ("settlement_offer", "settlement_accept"):
            facts["settlement_offer"] = self._settlement(rows, masks)
        if n.node in DATED and (dates := self._contract_dates(rows, masks)):
            facts["contract_dates"] = dates
        if any(f.status != "superseded" for f in d.financing):
            f = next(f for f in d.financing if f.status != "superseded")
            facts["notes"] = {"principal": usd(f.principal_cents),
                              "judgment_default": (f"final judgments for the payment of money above "
                                                   f"{usd(f.judgment_default_threshold_cents)} that \"remain "
                                                   f"undischarged, unpaid or unstayed for a period (during which "
                                                   f"execution shall not be effectively stayed) of "
                                                   f"{f.judgment_default_days} days\" (§7.01(i)), after notice by the "
                                                   f"trustee or holders of 25% of the notes"
                                                   if f.judgment_default_days else "none")}
        return facts

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
        at_end = bool(self.sens.get("settlement_date_in_interval"))
        monthly = self.m["settlement_scenarios"]["base"] == "monthly" or bool(self.sens.get("settlement_monthly"))
        out: dict = {}
        if (offer := self._pooled(rows, masks, "settle_offer")) is not None:
            out["amount"] = {"p5": usd(int(np.quantile(offer, 0.05))), "p50": usd(int(np.quantile(offer, 0.5)))}
        if self.draws is not None and self.draws.basis is not None and not at_end:
            need = self.draws.basis.need
            pd = [np.clip(r["day"] + int(p["value"]), 0, need.shape[1] - 1) for r in rows]
            vals = np.concatenate([need[np.arange(need.shape[0]), x][m] for x, m in zip(pd, masks, strict=True)])
            if vals.size:
                out["thirty_day_operating_need"] = usd(int(np.quantile(vals, 0.5)))
        out["basis"] = ("the company's available cash on the settlement date less its 30-day operating need, floored "
                        "at zero and capped at the amount owed")
        when = "at the end of the current stage of the dispute" if at_end else f"{int(p['value'])} days after the decision"
        out["payment"] = (f"equal monthly payments from the settlement date ({when}) to {fmt(self.horizon)}" if monthly
                          else f"one payment of the full amount on the settlement date, {when}")
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

    @staticmethod
    def _component(c, remit: dict) -> dict:
        """One judgment component as the record states it; the declared remittitur scenario beside the compensatory
        award where a remittitur is the question's premise or its outcome."""
        sealed = c.unknown and "sealed" in c.label.lower()
        out = {"component": c.label.split(";")[0].strip() if c.unknown else c.label, "status": c.status,
               "amount": usd(c.amount_cents) if c.amount_cents is not None else
               ("sealed; amount not public" if sealed else "computed by statute" if c.statutory else "unknown")}
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

    def _collateral(self, d: DisputeInstance, owed: np.ndarray) -> np.ndarray:
        """The cash collateral the law and surety practice require for a stay on each trajectory: the bond (the amount
        owed plus §1961 interest over the appeal) times the collateral share."""
        from app.analysis.events import rate_1961_bps

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
        s = self.spec[n.node]
        readings, read_from = self._readings(d, n.question_id)
        evidence, fids, record = self._evidence(d, n.node, read_from)
        verdict = n.node in VERDICT_NODES
        ctx = [] if verdict else context_phrases([c for c in n.context.split("|") if c], self.class_range,
                                                 self.labels(d))
        state = {"case": {"as_of": fmt(self.review), "analysis_period_ends": fmt(self.horizon),
                          "company": self.borrower, "counterparty": d.counterparty,
                          "obligation": f"{self.m['natures'].get(d.nature, d.nature)}, {d.order_reference}"},
                 "question": {"actor": s["actor"], "decision": s["decision"], "branches": list(n.branches),
                              "timing": s["timing"], "context": ctx},
                 "standard": self.standard(n.node),
                 "record_items": record, "path_facts": self.path_facts(n, d),
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
                            confidence=o.confidence, finding_ids=fids, readings=readings, evidence=st["evidence"],
                            observation_id=o.observation_id, path_facts=st["path_facts"])

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

    def add(self, step, edge, **kw) -> _S:
        return _S(self.steps + (step,), self.edges + ((edge,) if edge else ()),
                  **{**{k: getattr(self, k) for k in ("cls", "stayed", "appealed", "early", "a4", "notes_due")},
                     **kw})


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
        self.quiet = "continue" if self.pend else "neither"  # the branch that books nothing
        self.seek = "continue" if self.pend else "seek_sale_or_financing"  # the branch re-asked at the next milestone
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

    def inside(self, steps) -> bool:
        """Whether the last step's decision falls inside the horizon, before any petition, on some trajectory."""
        tr = self.fc.trace(self.d, steps)
        t = tr.day[-1]
        pet = np.where(tr.petition < 0, np.iinfo(np.int64).max, tr.petition)
        return bool(((t < self.N) & (t < pet)).any())

    def arises(self, s: _S, step) -> bool:
        return self.fc.arises(self.d, s.steps, step)

    def take(self, s: _S, step, edge, keys=(), **kw) -> _S:
        """Add a step; record its path facts (from the trace of the prefix plus this step) for the nodes it asks."""
        if keys:
            self.fc.record(keys, self.fc.trace(self.d, s.steps + (step,)))
        return s.add(step, edge, **kw)

    def court(self, s: _S, key: str, ctx: str) -> None:
        """A court's ruling on a motion gets the facts of its own day (events.py court_order): the stay's approval, with
        the security measured that day, or the registration order; the motion question keeps the motion day."""
        self.fc.record((key,), self.fc.trace(self.d, s.steps + (("court_order", ctx, ""),)))

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
        a3 = self.node("settlement_offer", interval, s.cls, s=s, probe=probe)
        q4 = self.node("settlement_accept", interval, s.cls, s=s, probe=probe, assumptions=("the company offers to settle for its available cash above its 30-day operating need",))
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
        for b, parts in self.fc.verdict_classes(self.d).items():
            y = s.add(("verdict", "I0", b), (composite(parts), "yes"), cls=b)
            if branches[b]["judgment"]:
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
        k = self.node("post_trial_motions", s.cls, s=s, probe=probe,
                      assumptions=("a money judgment is entered on the verdict",))
        self.settle(self.take(s, ("post_trial_motions", "", "yes"), (k, "yes"), (k,)), "I1", self.q1)
        self.post(self.take(s, probe, (k, "no"), (k,)))

    # I1: before the post-trial ruling
    def q1(self, s: _S) -> None:
        k = self.node("execute_pre_ruling", "I1", *self.cx(s), assumptions=("post-trial motions are pending",))
        self.stay_i1(self.take(s, ("execute_pre_ruling", "I1", "yes"), (k, "yes"), (k,)))
        self.ripe_i1(self.take(s, ("execute_pre_ruling", "I1", "no"), (k, "no"), (k,)))

    def stay_i1(self, s: _S) -> None:
        probe = ("stay", "I1", "no")
        a1 = self.node("stay_motion", "I1", s.cls, s=s, probe=probe,
                       assumptions=("the creditor executes before the ruling",))
        j8 = self.node("stay_approved", "I1", s.cls, s=s, probe=probe, assumptions=("the company moves for a stay",))
        self.court(s, j8, "stay_I1")
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

    def a4(self, s: _S, phase: str, then, on_file) -> None:
        """The company's response: on the levy day before the levy (I1, post), or at the post-ruling judgment
        default's ripe date after it sought a sale or financing (ripe)."""
        probe = (self.resp, phase, self.seek)
        pay = self.fc.pay_possible(self.d, s.steps, probe)
        rest = ("file", "continue") if self.pend else ("seek_sale_or_financing", "file", "neither")
        branches = (("pay",) if pay else ()) + rest
        pending = s.stayed and phase in ("I1", "post")  # moved for a stay, not yet approved
        when = {"post": ("the creditor levies on the company's cash that day",),
                "ripe": ("the judgment default under the notes has ripened that day",),
                "entry": ("the money judgment was entered that day, unpaid; execution is stayed automatically for "
                          "its first 30 days (Fed. R. Civ. P. 62(a))",)}.get(phase, ())
        k = self.node(self.resp, phase, s.cls, "pay" if pay else "nopay",
                      "after_seek" if s.a4 == "seek" else "first", *(("stay_pending",) if pending else ()),
                      s=s, probe=(self.resp, phase, self.quiet),
                      assumptions=(() if phase == "entry" else ("the judgment is enforceable, unstayed and unpaid",))
                      + when
                      + (("the company has moved for a stay, not yet approved",) if pending else ()), branches=branches)
        for b in branches:
            y = self.take(s, (self.resp, phase, b), (k, b), (k,), a4="seek" if b == self.seek else "closed")
            if b == "pay":
                self.tail(y, "paid")
            elif b == "file":
                on_file(y)
            else:
                then(y)

    def notes_petition(self, s: _S, phase: str, then) -> None:
        """The judgment default: the holders give notice and accelerate, then the issuer files, or else three holders
        file, or the notes stay due and unpaid. Paying the notes is removed by arithmetic."""
        f = self.fin
        if f is None or not f.judgment_default_days or not self.arises(s, ("judgment_default", phase, "no")):
            return then(s)
        probe = ("judgment_default", phase, "no")
        acc = ("judgment_default", phase, "accelerated")
        issuer, holders = (acc, ("notes_due_date", "issuer", "")), (acc, ("notes_due_date", "holders", ""))
        earlier = ("entered_not_acted",) if phase != "I1" and any(x[:2] == ("judgment_default", "I1")
                                                                  for x in s.steps) else ()
        h1 = self.node("holders_act_judgment", phase, s.cls, *earlier, s=s, probe=probe)
        a5 = self.node("petition_on_notes", f"judgment_{phase}", s=s, probe=issuer,
                       assumptions=("the holders accelerate the notes",))
        h3 = self.node("holders_involuntary", f"judgment_{phase}", s=s, probe=holders,
                       assumptions=("the notes are accelerated and unpaid", "the issuer does not file"))
        self.notes_facts(s, ((a5, issuer), (h3, holders)))
        classes = {"yes": [[(h1, "yes"), (a5, "yes")]],  # the issuer files on acceleration
                   "holders_file": [[(h1, "yes"), (a5, "no"), (h3, "yes")]],  # the holders file, per §7.06
                   "accelerated": [[(h1, "yes"), (a5, "no"), (h3, "no")]]}
        for branch, parts in self.unfiled(s, "judgment_default", phase, classes,
                                          (("holders_file", "accelerated"),)).items():
            y = self.take(s, ("judgment_default", phase, branch), (composite(parts), "yes"), (h1,), notes_due=True)
            if branch != "accelerated" and (self.fc.trace(self.d, y.steps).petition >= 0).all():
                self.floor(y, "petition")  # a petition on every trajectory
            else:
                then(y)
        then(self.take(s, probe, (composite([[(h1, "no")]]), "yes"), (h1,)))

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

    def ripe_i1(self, s: _S) -> None:
        self.notes_petition(s, "I1", self.ruling)


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
        """J2, one binary node per verdict branch: the money judgment stands, or is set aside (no money judgment on
        the path; any stay security released). A ruling after the period on every trajectory is not asked."""
        probe = ("post_trial_ruling", "", "stands")
        if not self.arises(s, probe):
            return self.tail(s, "motions_pending")
        k = self.node("post_trial_ruling", s.cls, s=s, probe=probe, assumptions=("post-trial motions are pending",))
        self.post(self.take(s, probe, (k, "stands"), (k,)))
        self.tail(self.take(s, ("post_trial_ruling", "", "set_aside"), (k, "set_aside"), (k,), cls="set_aside"),
                  "set_aside")

    def post(self, s: _S) -> None:
        self.settle(s, "I2", self.appeal)

    def appeal(self, s: _S) -> None:
        if s.appealed or not self.arises(s, ("appeal", "", "no")):
            return self.stay_post(s)
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
        a1 = self.node("stay_motion", "post", s.cls, s=s, probe=probe, assumptions=("the final judgment is entered",))
        j8 = self.node("stay_approved", "post", s.cls, s=s, probe=probe, assumptions=("the company moves for a stay",))
        self.court(s, j8, "stay_post")
        self.binary(s, "stay", "post", [[(a1, "yes"), (j8, "yes")]], (a1,),
                    lambda y: self.enforce(replace(y, stayed=True), self.stayed_tail, pending=True), self.i3)

    def stayed_tail(self, s: _S) -> None:
        """Stayed on approval: the I4 settlement, then the notes' judgment default where it can ripen first."""
        self.settle(s, "I4", lambda z: self.notes_petition(z, "post", lambda y: self.tail(y, "stayed")))

    def i3(self, s: _S) -> None:
        self.settle(s, "I3", self.enforce)

    def a4_post(self, s: _S, then) -> None:
        """The company's response on the day the creditor's levy falls, before the levy."""
        if s.a4 == "closed" or not self.arises(s, (self.resp, "post", self.quiet)):
            return then(s)
        self.a4(s, "post", then, lambda y: self.tail(y, "petition"))

    def enforce(self, s: _S, then=None, pending: bool = False) -> None:
        """The creditor enforces (with early registration before finality). pending: the debtor has moved for a stay not yet approved; a levy counts only
        before approval, so the question is asked where it moves cash on some trajectory."""
        then = then or self.ripe_post
        levy_step, none_step = ("enforce", "post", "levy"), ("enforce", "post", "none")
        if not self.arises(s, none_step) or (pending and not self.fc.moves_cash(self.d, s.steps, levy_step, none_step)):
            return then(s)
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
        self.a4_post(self.take(s, levy_step, (composite(levy), "yes"), keys), then)
        then(self.take(s, none_step, (composite(none), "yes"), keys))

    def ripe_post(self, s: _S) -> None:
        """After 'seek a sale or financing', the company responds again at the ripe default date."""
        after = lambda y: self.notes_petition(y, "post", lambda z: self.tail(z, "unresolved"))  # noqa: E731
        if s.a4 == "seek" and self.arises(s, (self.resp, "ripe", self.quiet)):
            return self.a4(s, "ripe", after, lambda y: self.tail(y, "petition"))
        after(s)

    # the listing chain, then the cash floor
    def tail(self, s: _S, outcome: str) -> None:
        """The listing chain reaches collections only through the notes: once they are due and unpaid, or once a
        petition precedes it, it moves nothing and the path goes to the cash floor."""
        f = self.fin
        probe = ("listing", "", "listed")
        if f is None or f.listing_deadline is None or not self.inside(s.steps + (probe,)):
            return self.floor(s, outcome)  # no listing chain, or an acceleration or petition precedes it
        dates = _listing_dates(self.fc, self.d)
        if min(dates["delisted_panel"], dates["delisted_suspension"]) >= self.N:
            return self.floor(s, outcome)  # delisting falls after the horizon
        if "listing_kept" in self.fc.spec and dates["panel_decision"] >= self.N:
            return self.kept(s, dates, outcome)  # listing_route: the company's hearing request alone decides
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
        k = self.node("listing_kept", s=s, probe=at, assumptions=("the stock is not compliant on the deadline",))
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
        h2 = self.node("holders_act_delisting", dc, s=s, probe=probe,
                       assumptions=("the stock is not listed on an Eligible Market",))
        acc = ("delisting_notes", dc, "accelerated")
        issuer, holders = (acc, ("notes_due_date", "issuer", "")), (acc, ("notes_due_date", "holders", ""))
        a5 = self.node("petition_on_notes", f"delisting_{dc}", s=s, probe=issuer,
                       assumptions=("the holders accelerate the notes",))
        h3 = self.node("holders_involuntary", f"delisting_{dc}", s=s, probe=holders,
                       assumptions=("the notes are accelerated and unpaid", "the issuer does not file"))
        facts = [(a5, issuer), (h3, holders)]
        classes = {"petition_delist": [[(h2, "accelerate"), (a5, "yes")]],
                   "petition_delist_holders": [[(h2, "accelerate"), (a5, "no"), (h3, "yes")]],
                   "accelerated": [[(h2, "accelerate"), (a5, "no"), (h3, "no")]]}
        none = [[(h2, "neither")]]
        if Chain_(self.fc, self.d).repurchase_day(delist) < self.N:
            rep = ("delisting_notes", dc, "repurchase_unpaid")
            r_issuer, r_holders = (rep, ("notes_due_date", "issuer", "")), (rep, ("notes_due_date", "holders", ""))
            a5r = self.node("petition_on_notes", f"repurchase_{dc}", s=s, probe=r_issuer,
                            assumptions=("the repurchase falls due unpaid",))
            h3r = self.node("holders_involuntary", f"repurchase_{dc}", s=s, probe=r_holders,
                            assumptions=("the repurchase is unpaid", "the issuer does not file"))
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

    def floor(self, s: _S, outcome: str) -> None:
        """The first day available cash falls below the 30-day operating need: the company files or keeps operating;
        where the case sets raise_capacity, it may also raise equity ('raise_equity' only where the amount available
        is positive on some trajectory), and zero cash stays the fallback decision."""
        if self.fc.raising:
            return self.financing(s, outcome)
        probe = ("cash_floor", "", "no")
        if not self.inside(s.steps + (probe,)):
            return self.emit(s, outcome)
        k = self.node("petition_cash_floor", s=s, probe=probe)
        self.fc.record((k,), self.fc.trace(self.d, s.steps + (probe,)))
        self.emit(s.add(("cash_floor", "", "yes"), (k, "yes")), "petition")
        self.cash_out(s.add(probe, (k, "no")), outcome)

    def financing(self, s: _S, outcome: str) -> None:
        probe = ("cash_floor", "", "continue")
        if not self.inside(s.steps + (probe,)):
            return self.emit(s, outcome)
        tr = self.fc.trace(self.d, s.steps + (probe,))
        can = bool(((tr.day[-1] < self.N) & (tr.raise_offer > 0)).any())
        branches = (("raise_equity",) if can else ()) + ("file", "continue")
        k = self.node("financing_at_floor", "raise" if can else "noraise", s=s, probe=probe, branches=branches)
        self.fc.record((k,), tr)
        for b in branches:
            y = s.add(("cash_floor", "", b), (k, b))
            self.emit(y, "petition") if b == "file" else self.cash_out(y, outcome)

    def cash_out(self, s: _S, outcome: str) -> None:
        """The first day available cash falls below zero, after the company kept operating at the floor."""
        probe = ("cash_out", "", "no")
        if not self.inside(s.steps + (probe,)):
            return self.emit(s, outcome)
        k = self.node("petition_cash_out", "cash_exhausted", s=s, probe=probe)
        self.fc.record((k,), self.fc.trace(self.d, s.steps + (probe,)))
        self.emit(s.add(("cash_out", "", "yes"), (k, "yes")), "petition")
        self.emit(s.add(probe, (k, "no")), outcome)

    def emit(self, s: _S, outcome: str) -> None:
        self.out.append(DisputePath(instance_id=self.d.instance_id, steps=s.steps, outcome=outcome, edges=s.edges))


class _BankWalk:
    """The bank view's chain: the company decides whether to file on the first date its available cash falls below
    its 30-day operating need, and, where it keeps operating, again on the first date its cash falls below zero."""

    def __init__(self, fc: Forecaster) -> None:
        from app.analysis.events import BANK

        self.fc, self.out, self.bank = fc, [], BANK

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
        if self.fc.raising:
            return self.run_financing()
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

    def run_financing(self) -> list[DisputePath]:
        """As `run`, with the financing decision at the floor (financing_at_floor): raise equity, file or continue;
        after a raise or 'continue', the decision at zero cash."""
        floor, out = ("cash_floor", "", "continue"), ("cash_out", "", "no")
        if not self.inside((floor,)):
            self.emit((), (), "operating")
            return self.out
        tr = self.fc.bank_trace((floor,))
        can = bool(((tr.day[-1] < self.fc.days) & (tr.raise_offer > 0)).any())
        branches = (("raise_equity",) if can else ()) + ("file", "continue")
        self.probe = {"financing_at_floor": (floor,)}
        k = self.node("financing_at_floor", "raise" if can else "noraise", branches=branches)
        for b in branches:
            at = ("cash_floor", "", b)
            if b == "file":
                self.emit((at,), ((k, b),), "petition")
                continue
            if not self.inside((at, out)):
                self.emit((at,), ((k, b),), "operating")
                continue
            self.probe["petition_cash_out"] = (at, out)
            k2 = self.node("petition_cash_out", "cash_exhausted", *(("raised",) if b == "raise_equity" else ()))
            self.emit((at, ("cash_out", "", "yes")), ((k, b), (k2, "yes")), "petition")
            self.emit((at, out), ((k, b), (k2, "no")), "operating")
        return self.out


def bank_state(fc: Forecaster, n: Node) -> dict:
    """The bank view's question: the company, its decision, the decision dates, its projected available cash and
    30-day operating need at the decision, the law that governs it and the notes' coupon (a common borrower input).
    It differs from the research view's question only in the research facts."""
    s = fc.spec[n.node]
    facts: dict = {}
    rows = fc.bank_facts.get(n.key, [])
    if rows:
        day, cash, need = (np.concatenate([r[i] for r in rows]) for i in range(3))
        eq = np.concatenate([r[3] for r in rows if len(r) > 3 and r[3] is not None]) \
            if any(len(r) > 3 and r[3] is not None for r in rows) else None
        inside = day < fc.days
        if inside.any():
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
    ctx = context_phrases([c for c in n.context.split("|")[1:] if c], {})
    return {"case": {"as_of": fmt(fc.review), "analysis_period_ends": fmt(fc.horizon), "company": "the company"},
            "question": {"actor": s["actor"], "decision": s["decision"], "branches": list(n.branches),
                         "timing": s["timing"], "context": ctx},
            "standard": fc.standard(n.node), "record_items": [], "path_facts": facts, "assumptions": [],
            "evidence": [], "readings": {}}


def Chain_(fc: Forecaster, d: DisputeInstance):
    from app.analysis.events import Chain

    return Chain(d, fc.setup, fc.m, fc.draws, fc.sens)


def _listing_dates(fc: Forecaster, d: DisputeInstance) -> dict[str, int]:
    return Chain_(fc, d).listing_dates()
