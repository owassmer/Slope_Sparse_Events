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


class Dist(dict):
    """Node key -> branch distribution; composite keys are computed on demand by the chain rule."""

    def __missing__(self, key: str) -> dict[str, float]:
        if not key.startswith(COMPOSITE):
            raise KeyError(key)
        p = sum(math.prod(self[k][b] for k, b in c) for c in json.loads(key[1:]))
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
    stay_offer: np.ndarray | None = None  # cash above the 30-day operating need on the stay-motion day (0: none)
    triggers: dict | None = None  # events.TRIGGERS name -> day index per draw (events.BIG: none)

    @classmethod
    def of(cls, tr) -> _Prefix:
        ev = tr.events
        h = hashlib.blake2b(digest_size=32)
        for a in (ev.cash, ev.lock, ev.capacity, ev.petition):
            h.update(np.ascontiguousarray(a).tobytes())
        return cls(tr.day, tr.cash, tr.owed, tr.collateral, ev.petition.copy(), h.digest(),
                   None if tr.cause is None else tr.cause.copy(), tr.marks, tr.settle_offer, tr.stay_offer,
                   tr.triggers)


INTERVAL_PHRASES = {"I1": "before the post-trial ruling", "I2": "after the post-trial ruling, before the appeal deadline",
                    "I3": "judgment enforceable and unstayed", "I4": "judgment stayed on approved security",
                    "post": "after the post-trial ruling"}
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
                 "entered_not_acted": "the holders have not given notice of a default on the judgment as entered"}


def context_phrases(tags: list[str], ranges: dict[str, tuple[int, int]]) -> list[str]:
    """The situation a decision is asked in, in plain terms (the node's context tags rendered for Jev)."""
    out = []
    for t in tags:
        if t in INTERVAL_PHRASES:
            out.append(INTERVAL_PHRASES[t])
        elif t in STATE_PHRASES:
            out.append(STATE_PHRASES[t])
        elif t in ("pay", "nopay"):  # carried by the options offered
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


MERITS = ("ts_liability_jmol", "ts_damages_ruling", "remittitur_accepted", "patent_jmol", "trebling", "fees_awarded",
          "prejudgment_interest", "injunction")


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
        self.reach = int(basis.cash.max()) if basis is not None else None  # no trajectory holds more cash than this
        self.class_members: dict[str, list[tuple[int, int]]] = {}  # ruling class -> (total, fees) of each outcome
        self.class_range: dict[str, tuple[int, int]] = {}  # merged amount class label -> (min, max) judgment
        self.nodes: dict[str, Node] = {}
        self.facts: dict[str, list] = {}  # node key -> [(day, cash, owed, collateral) arrays] pooled over paths
        self._traces: dict = {}
        self.bank_nodes: dict[str, Node] = {}  # the bank view's questions (bank_state)
        self.bank_facts: dict[str, list] = {}  # bank node key -> [(day, cash, need) arrays]

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
        out = {}
        for (c0, retrial), parts in classes.items():
            c = (c0, retrial)
            lo = min(members[c])
            label = f"{c0}:{lo[0]}:{lo[1]}" if c0.startswith("beyond") else c0
            label = ("retrial" if label == "none" else f"{label}:retrial") if retrial else label
            out[label] = parts
            self.class_members[label] = members[c]
            if c0.startswith("beyond"):  # what Jev is told: the class's range, never one figure
                self.class_range[_label(label)] = (lo[0], max(members[c])[0])
        return out


    # --- the chain walk ---------------------------------------------------------------------------------------------

    def paths(self, d: DisputeInstance) -> list[DisputePath]:
        """Every structurally feasible path through the dispute's chains (see the module docstring)."""
        if d.borrower_role != "debtor" or d.stage not in ("post_trial", "judgment_entered", "enforcement",
                                                           "appeal_filed", "appeal_pending"):
            return [DisputePath(instance_id=d.instance_id, steps=(), outcome="outside_chains", edges=())]
        W = _Walk(self, d)
        return W.run()

    def all_paths(self) -> dict[str, dict[str, list[DisputePath]]]:
        return {d.instance_id: {"": self.paths(d)} for d, _ in self.ordered()}

    def record(self, keys, tr) -> None:
        for k in keys:
            self.facts.setdefault(k, []).append((tr.day[-1], tr.cash[-1], tr.owed[-1], tr.collateral[-1]))


    # --- the residual questions' state and Jev --------------------------------------------------------------------

    def _date(self, t: float) -> str:
        return fmt(self.review + timedelta(days=int(t) + 1))

    def path_facts(self, n: Node, d: DisputeInstance) -> dict:
        """What code computed for this node, pooled over the paths that reach it, before any Jev answer."""
        reg = registry_entry(n.question_id)
        remitted = self.m["remittitur_scenarios"]["scenarios"].get("remitted", {}).get("amount_cents")
        facts: dict = {"components": [{"component": c.label, "status": c.status,
                                       "amount": usd(c.amount_cents) if c.amount_cents is not None else
                                       ("computed by statute" if c.statutory else "unknown"),
                                       **({"remitted_amount": usd(remitted)} if remitted and c.kind == "compensatory"
                                          else {})}
                                      for c in d.components]}
        if n.question_id in self.no_cash:
            return facts
        rows = self.facts.get(n.key, [])
        if not rows:
            return facts
        day = np.concatenate([r[0] for r in rows])
        inside = day < self.days
        if not inside.any():
            return facts
        cash, owed, coll = (np.concatenate([r[i] for r in rows])[inside] for i in (1, 2, 3))
        facts["decision_date"] = {"p5": self._date(np.quantile(day[inside], 0.05)),
                                  "p50": self._date(np.quantile(day[inside], 0.5)),
                                  "p95": self._date(np.quantile(day[inside], 0.95))}
        facts["cash_balance_at_decision"] = {"p5": usd(int(np.quantile(cash, 0.05))),
                                             "p50": usd(int(np.quantile(cash, 0.5)))}
        facts["amount_owed_at_decision"] = {"p50": usd(int(np.quantile(owed, 0.5))),
                                            "max": usd(int(owed.max()))}
        merged = next((self.class_range[c] for c in n.context.split("|") if c in self.class_range), None)
        if merged is not None:  # a merged class: its range of judgment amounts, not the representative's figure
            facts["amount_owed_at_decision"] = {
                "judgment_after_ruling": {"min": usd(merged[0]), "max": usd(merged[1])},
                "note": "the range across these post-trial ruling outcomes; post-judgment interest accrues, less any "
                        "amount collected"}
        if reg.get("node") in ("stay_motion", "stay_approved"):
            facts["bond_collateral_required"] = usd(int(np.quantile(self._collateral(d, owed), 0.5)))
        if any(f.status != "superseded" for f in d.financing):
            f = next(f for f in d.financing if f.status != "superseded")
            facts["notes"] = {"principal": usd(f.principal_cents),
                              "judgment_default": (f"final money judgments above {usd(f.judgment_default_threshold_cents)}"
                                                   f" unpaid or unstayed for {f.judgment_default_days} days, after "
                                                   f"notice" if f.judgment_default_days else "none")}
        return facts

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

    def _evidence(self, d: DisputeInstance, node: str) -> tuple[list[dict], tuple[str, ...], list[dict]]:
        """The accepted findings that supply the question's record items, each passage with the items it supplies;
        and the record items, each marked as in the record or not."""
        items = self.spec[node]["record_items"]
        supplies: dict[str, list[str]] = {}
        for item in items:
            for fid in self.slots.get(node, {}).get(item, []):
                if fid in self.findings:
                    supplies.setdefault(fid, []).append(item)
        evidence = [{**self.hydrate(self.findings[fid]), "supplies": its} for fid, its in supplies.items()]
        record = [{"item": x, "in_the_record": any(x in its for its in supplies.values())} for x in items]
        return evidence, tuple(supplies), record

    def _readings(self, d: DisputeInstance, qid: str) -> dict:
        factors, out = self._routed(qid), {}
        for f in d.factors:
            if f.factor_id not in factors:
                continue
            if f.kind == "present" and f.probability is not None:
                out[f.label] = {"probability_present": round(f.probability, 3)}
            elif f.distribution:
                out[f.label] = {"distribution": {k: round(v, 3) for k, v in f.distribution.items()},
                                **({"conflicting_readings": True} if f.conflict else {}),
                                **({"passage_dated": f.decisive.source_date} if f.decisive else {})}
        return out

    def state(self, n: Node) -> tuple[dict, tuple[str, ...], dict]:
        d = next(x for x in self.disputes if x.instance_id == n.instance_id)
        s = self.spec[n.node]
        evidence, fids, record = self._evidence(d, n.node)
        readings = self._readings(d, n.question_id)
        ctx = context_phrases([c for c in n.context.split("|") if c], self.class_range)
        terms = {k: v for t in self.m["templates"].values() for k, v in t.get("terms_from_instrument", {}).items()}
        state = {"case": {"as_of": fmt(self.review), "analysis_period_ends": fmt(self.horizon),
                          "borrower": self.borrower, "counterparty": d.counterparty,
                          "obligation": f"{self.m['natures'].get(d.nature, d.nature)}, {d.order_reference}"},
                 "question": {"actor": s["actor"], "decision": s["decision"], "branches": list(n.branches),
                              "timing": s["timing"], "context": ctx},
                 "standard": [self.m["rules"][r]["citation"] if r in self.m["rules"] else terms.get(r, r)
                              for r in s["standard"]],
                 "record_items": record, "path_facts": self.path_facts(n, d),
                 "assumptions": list(n.assumptions), "evidence": evidence, "readings": readings}
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
        held, never = self.fc.situation(self.d, s.steps + (probe,), conds)
        out = []
        for c in conds:
            if c == "ruled":
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
        q4 = self.node("settlement_accept", interval, s.cls, s=s, probe=probe, assumptions=("the judgment debtor offers to settle for its available cash above its 30-day operating need",))
        self.binary(s, "settle", interval, [[(a3, "yes"), (q4, "yes")]], (a3, q4),
                    lambda y: self.tail(y, "settled"), then_no)

    def run(self) -> list[DisputePath]:
        d, s = self.d, _S()
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

    # I1: before the post-trial ruling
    def q1(self, s: _S) -> None:
        k = self.node("execute_pre_ruling", "I1", assumptions=("post-trial motions are pending",))
        self.stay_i1(self.take(s, ("execute_pre_ruling", "I1", "yes"), (k, "yes"), (k,)))
        self.ripe_i1(self.take(s, ("execute_pre_ruling", "I1", "no"), (k, "no"), (k,)))

    def stay_i1(self, s: _S) -> None:
        probe = ("stay", "I1", "no")
        a1 = self.node("stay_motion", "I1", s.cls, s=s, probe=probe,
                       assumptions=("the creditor executes before the ruling",))
        j8 = self.node("stay_approved", "I1", s.cls, s=s, probe=probe, assumptions=("the debtor moves for a stay",))
        self.binary(s, "stay", "I1", [[(a1, "yes"), (j8, "yes")]], (a1, j8),
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
        k = self.node("registration_early", "I1", s=s, probe=("registration_early", "I1", "no"),
                      assumptions=("the creditor executes before finality",))
        self.a4_i1(self.take(s, ("registration_early", "I1", "yes"), (k, "yes"), (k,), early=True))
        self.ripe_i1(self.take(s, ("registration_early", "I1", "no"), (k, "no"), (k,)))

    def a4_i1(self, s: _S) -> None:
        """The debtor's response on the levy day (order + levy_lag_days), where it falls inside the horizon, before stay approval and
        before the ruling on some trajectory (events.py debtor_response)."""
        if not self.arises(s, ("debtor_response", "I1", "neither")):
            return self.ripe_i1(s)
        self.a4(s, "I1", self.ripe_i1, lambda y: self.emit(y, "petition"))

    def a4(self, s: _S, phase: str, then, on_file) -> None:
        probe = ("debtor_response", phase, "seek_sale_or_financing")
        pay = self.fc.pay_possible(self.d, s.steps, probe)
        branches = (("pay",) if pay else ()) + ("seek_sale_or_financing", "file", "neither")
        pending = s.stayed and phase == "I1"  # moved for a stay, not yet approved
        k = self.node("debtor_response", phase, s.cls, "pay" if pay else "nopay",
                      "after_seek" if s.a4 == "seek" else "first", *(("stay_pending",) if pending else ()),
                      s=s, probe=("debtor_response", phase, "neither"), assumptions=("the judgment is enforceable, unstayed and unpaid",)
                      + (("the debtor has moved for a stay, not yet approved",) if pending else ()), branches=branches)
        for b in branches:
            y = self.take(s, ("debtor_response", phase, b), (k, b), (k,),
                          a4="seek" if b.startswith("seek") else "closed")
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
        earlier = ("entered_not_acted",) if phase != "I1" and any(x[:2] == ("judgment_default", "I1")
                                                                  for x in s.steps) else ()
        h1 = self.node("holders_act_judgment", phase, s.cls, *earlier, s=s, probe=probe)
        a5 = self.node("petition_on_notes", f"judgment_{phase}", s=s, probe=probe,
                       assumptions=("the holders accelerate the notes",))
        h3 = self.node("holders_involuntary", f"judgment_{phase}", s=s, probe=probe,
                       assumptions=("the notes are accelerated and unpaid", "the issuer does not file"))
        keys = (h1, a5, h3)
        petition = [[(h1, "yes"), (a5, "yes")], [(h1, "yes"), (a5, "no"), (h3, "yes")]]
        y = self.take(s, ("judgment_default", phase, "yes"), (composite(petition), "yes"), keys)
        if (self.fc.trace(self.d, y.steps).petition >= 0).all():  # a petition on every trajectory
            self.floor(y, "petition")
        else:
            then(y)
        then(self.take(s, ("judgment_default", phase, "accelerated"),
                       (composite([[(h1, "yes"), (a5, "no"), (h3, "no")]]), "yes"), keys, notes_due=True))
        then(self.take(s, probe, (composite([[(h1, "no")]]), "yes"), keys))

    def ripe_i1(self, s: _S) -> None:
        self.notes_petition(s, "I1", self.ruling)


    # the ruling and after
    def ruling(self, s: _S) -> None:
        for c, parts in self.fc.ruling_classes(self.d).items():
            y = s.add(("ruling", "", c), (composite(parts), "yes"), cls=_label(c))
            if c == "none":
                self.tail(y, "vacated")
            elif c == "retrial":
                self.tail(y, "new_trial")
            else:
                self.post(y)

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
        j8 = self.node("stay_approved", "post", s.cls, s=s, probe=probe, assumptions=("the debtor moves for a stay",))
        self.binary(s, "stay", "post", [[(a1, "yes"), (j8, "yes")]], (a1, j8),
                    lambda y: self.enforce(replace(y, stayed=True), self.stayed_tail, pending=True), self.i3)

    def stayed_tail(self, s: _S) -> None:
        """Stayed on approval: the I4 settlement, then the notes' judgment default where it can ripen first."""
        self.settle(s, "I4", lambda z: self.notes_petition(z, "post", lambda y: self.tail(y, "stayed")))

    def i3(self, s: _S) -> None:
        self.settle(s, "I3", self.a4_post)

    def a4_post(self, s: _S) -> None:
        if s.a4 == "closed" or not self.arises(s, ("debtor_response", "post", "neither")):
            return self.enforce(s)
        self.a4(s, "post", self.enforce, lambda y: self.tail(y, "petition"))

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
                       + (("the debtor has moved for a stay, not yet approved",) if pending else ()))
        if s.appealed and not s.early:
            j9 = self.node("registration_early", "post", s.cls, *extra, s=s, probe=none_step,
                           assumptions=("the creditor enforces before finality",))
            levy, none, keys = [[(q3, "yes"), (j9, "yes")]], [[(q3, "no")], [(q3, "yes"), (j9, "no")]], (q3, j9)
        else:
            levy, none, keys = [[(q3, "yes")]], [[(q3, "no")]], (q3,)
        then(self.take(s, levy_step, (composite(levy), "yes"), keys))
        then(self.take(s, none_step, (composite(none), "yes"), keys))

    def ripe_post(self, s: _S) -> None:
        self.notes_petition(s, "post", lambda y: self.tail(y, "unresolved"))

    # the listing chain, then the cash floor
    def tail(self, s: _S, outcome: str) -> None:
        """The listing chain reaches collections only through the notes: once they are due and unpaid, or once a
        petition precedes it, it moves nothing and the path goes to the cash floor."""
        f = self.fin
        probe = ("listing", "", "listed")
        if f is None or f.listing_deadline is None or s.notes_due or not self.inside(s.steps + (probe,)):
            return self.floor(s, outcome)
        dates = _listing_dates(self.fc, self.d)
        if min(dates["delisted_panel"], dates["delisted_suspension"]) >= self.N:
            return self.floor(s, outcome)  # delisting falls after the horizon
        a7 = self.node("reverse_split_board", s=s, probe=probe)
        st1 = self.node("split_approved", s=s, probe=probe, assumptions=("the board calls the vote in time",))
        a8 = self.node("nasdaq_hearing", s=s, probe=probe, assumptions=("the stock is not compliant on the deadline",))
        n1 = self.node("panel_exception", s=s, probe=probe, assumptions=("the issuer requests a hearing",))
        not_ok = [[(a7, "no")], [(a7, "yes"), (st1, "no")]]
        classes = {"listed": [[(a7, "yes"), (st1, "yes")]] + [c + [(a8, "yes"), (n1, "yes")] for c in not_ok],
                   "delisted_panel": [c + [(a8, "yes"), (n1, "no")] for c in not_ok],
                   "delisted_suspension": [c + [(a8, "no")] for c in not_ok]}
        for c in ("delisted_panel", "delisted_suspension"):
            if dates[c] >= self.N:  # delisted only after the horizon: listed throughout it
                classes["listed"] += classes.pop(c)
        keys = (a7, st1, a8, n1)
        for c, parts in classes.items():
            y = self.take(s, ("listing", "", c), (composite(parts), "yes"), keys)
            if c == "listed":
                self.floor(y, outcome)
            else:
                self.delisting_notes(y, c, dates[c], outcome)

    def delisting_notes(self, s: _S, dc: str, delist: int, outcome: str) -> None:
        """Delisting is an Event of Default and a Fundamental Change. The holders accelerate, require the repurchase,
        or neither; then the issuer files, or else three holders file, or the notes stay due and unpaid."""
        probe = ("delisting_notes", dc, "none")
        h2 = self.node("holders_act_delisting", dc, s=s, probe=probe,
                       assumptions=("the stock is not listed on an Eligible Market",))
        a5 = self.node("petition_on_notes", f"delisting_{dc}", s=s, probe=probe,
                       assumptions=("the holders accelerate the notes",))
        h3 = self.node("holders_involuntary", f"delisting_{dc}", s=s, probe=probe,
                       assumptions=("the notes are accelerated and unpaid", "the issuer does not file"))
        classes = {"petition_delist": [[(h2, "accelerate"), (a5, "yes")], [(h2, "accelerate"), (a5, "no"), (h3, "yes")]],
                   "accelerated": [[(h2, "accelerate"), (a5, "no"), (h3, "no")]]}
        none = [[(h2, "neither")]]
        keys = [h2, a5, h3]
        if Chain_(self.fc, self.d).repurchase_day(delist) < self.N:
            a5r = self.node("petition_on_notes", f"repurchase_{dc}", s=s, probe=probe,
                            assumptions=("the repurchase falls due unpaid",))
            h3r = self.node("holders_involuntary", f"repurchase_{dc}", s=s, probe=probe,
                            assumptions=("the repurchase is unpaid", "the issuer does not file"))
            classes["petition_repurchase"] = [[(h2, "repurchase_only"), (a5r, "yes")],
                                              [(h2, "repurchase_only"), (a5r, "no"), (h3r, "yes")]]
            classes["repurchase_unpaid"] = [[(h2, "repurchase_only"), (a5r, "no"), (h3r, "no")]]
            keys += [a5r, h3r]
        else:  # the repurchase date falls after the horizon: requiring it moves nothing inside it
            none.append([(h2, "repurchase_only")])
        classes["none"] = none
        for c, parts in classes.items():
            y = self.take(s, ("delisting_notes", dc, c), (composite(parts), "yes"), keys,
                          notes_due=c in ("accelerated", "repurchase_unpaid"))
            self.floor(y, outcome)

    def floor(self, s: _S, outcome: str) -> None:
        """The first day available cash falls below the 30-day operating need: the company files or keeps operating."""
        probe = ("cash_floor", "", "no")
        if not self.inside(s.steps + (probe,)):
            return self.emit(s, outcome)
        k = self.node("petition_cash_floor", s=s, probe=probe)
        self.fc.record((k,), self.fc.trace(self.d, s.steps + (probe,)))
        self.emit(s.add(("cash_floor", "", "yes"), (k, "yes")), "petition")
        self.cash_out(s.add(probe, (k, "no")), outcome)

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

    def node(self, name: str, *ctx: str) -> str:
        k = f"{self.bank}:{name}|" + "|".join((self.bank, *ctx))
        if k not in self.fc.bank_nodes:
            s = self.fc.spec[name]
            self.fc.bank_nodes[k] = Node(key=k, instance_id=self.bank, node=name, context="|".join((self.bank, *ctx)),
                                         cls="", question_id=s["residual_question"], event=s["decision"],
                                         assumptions=(), window=s["timing"], branches=tuple(s["branches"]))
            tr = self.fc.bank_trace(self.probe[name])
            t = tr.day[-1]
            need = self.fc.draws.basis.need[np.arange(len(t)), np.clip(t, 0, self.fc.days - 1)]
            self.fc.bank_facts.setdefault(k, []).append((t, tr.cash[-1], need))
        return k

    def emit(self, steps: tuple, edges: tuple, outcome: str) -> None:
        self.out.append(DisputePath(instance_id=self.bank, steps=steps, outcome=outcome, edges=edges))

    def run(self) -> list[DisputePath]:
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


def bank_state(fc: Forecaster, n: Node) -> dict:
    """The bank view's question: the company, its decision, the decision dates, and its projected available cash and
    30-day operating need at the decision, from the bank data and the common borrower inputs."""
    s = fc.spec[n.node]
    facts: dict = {}
    rows = fc.bank_facts.get(n.key, [])
    if rows:
        day, cash, need = (np.concatenate([r[i] for r in rows]) for i in range(3))
        inside = day < fc.days
        if inside.any():
            q = lambda x, p: usd(int(np.quantile(x[inside], p)))  # noqa: E731
            facts = {"decision_date": {k: fc._date(np.quantile(day[inside], p))
                                       for k, p in (("p5", 0.05), ("p50", 0.5), ("p95", 0.95))},
                     "cash_balance_at_decision": {"p5": q(cash, 0.05), "p50": q(cash, 0.5)},
                     "operating_need_30_days_at_decision": {"p50": q(need, 0.5)}}
    ctx = context_phrases([c for c in n.context.split("|")[1:] if c], {})
    return {"case": {"as_of": fmt(fc.review), "borrower": fc.borrower},
            "question": {"actor": s["actor"], "decision": s["decision"], "branches": list(n.branches),
                         "timing": s["timing"], "context": ctx},
            "standard": [], "record_items": [], "path_facts": facts, "assumptions": [], "evidence": [], "readings": {}}


def Chain_(fc: Forecaster, d: DisputeInstance):
    from app.analysis.events import Chain

    return Chain(d, fc.setup, fc.m, fc.draws, fc.sens)


def _listing_dates(fc: Forecaster, d: DisputeInstance) -> dict[str, int]:
    return Chain_(fc, d).listing_dates()
