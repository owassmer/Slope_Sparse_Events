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
import itertools
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Protocol

import numpy as np

from app.disputes.interpret import ESTABLISHED
from app.disputes.rules import load_model
from app.domain.investigation import AtomicFinding, DisputeInstance, SemanticObservation
from app.domain.values import usd

MAX_PASSAGES = 6
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



def load_registry() -> dict:
    from app.config import question_registry

    return question_registry()


def registry_entry(qid: str) -> dict:
    return next((q for q in load_registry()["questions"] if q["id"] == qid), {})


def _q(model: dict) -> dict[str, dict]:
    """Chain node name -> its template spec (all templates)."""
    return {n: s for t in model["templates"].values() for n, s in t.get("nodes", {}).items()}


MERITS = ("ts_liability_jmol", "ts_damages_ruling", "remittitur_accepted", "patent_jmol", "trebling", "fees_awarded",
          "prejudgment_interest", "injunction")


class Forecaster:
    def __init__(self, disputes: list[DisputeInstance], findings: dict[str, AtomicFinding], *, borrower: str,
                 review: date, horizon: date, hydrate: Callable[[AtomicFinding], dict], model: dict | None = None,
                 setup=None, basis=None, sens: dict | None = None) -> None:
        from app.analysis.events import Draws

        self.m = model or load_model()
        self.spec = _q(self.m)
        self.disputes = [d for d in disputes if d.status == "interpreted"]
        self.findings, self.borrower, self.review, self.horizon, self.hydrate = findings, borrower, review, horizon, hydrate
        self.setup, self.sens = setup, sens or {}
        self.days = (horizon - review).days
        self.draws = Draws(basis.cash.shape[0], basis=basis) if basis is not None else None
        self.reach = int(basis.cash.max()) if basis is not None else None  # no trajectory holds more cash than this
        self.nodes: dict[str, Node] = {}
        self.facts: dict[str, list] = {}  # node key -> [(day, cash, owed, collateral) arrays] pooled over paths
        self._traces: dict = {}

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

    def trace(self, d: DisputeInstance, steps: tuple):
        from app.analysis.events import event_trace

        key = (d.instance_id, steps)
        if key not in self._traces:
            path = DisputePath(instance_id=d.instance_id, steps=steps, outcome="", edges=())
            self._traces[key] = event_trace(d, path, self.setup, self.m, self.draws, self.sens)
        return self._traces[key]

    def arises(self, d: DisputeInstance, steps: tuple, step: tuple) -> bool:
        """Whether the decision can fall inside the horizon on some trajectory of the path (else its window is
        closed and it is not asked)."""
        tr = self.trace(d, steps + (step,))
        return bool((tr.day[-1] < self.days).any())

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
            if c is not None and c.motion:
                out[node] = () if kind == "patent" else ("trade-secret money survives the ruling",)
        if "injunction" in by_kind:
            out["injunction"] = ()
        return {n: self.node(d, n, assumptions=a) for n, a in out.items()}

    def ruling_classes(self, d: DisputeInstance) -> dict[str, list[list[tuple[str, str]]]]:
        """Each ruling outcome's conjunction of merits answers, grouped into amount classes by arithmetic: 'none',
        each amount some trajectory could fund, and 'beyond' (collateral at its lower bound exceeds the most cash
        any trajectory holds, so pay, a self-funded bond, the levy and the settlement bound are identical)."""
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
        lower = self.m["parameters"]["bond_collateral_share_bps"]["lower"] / 10_000
        classes: dict[str, list] = {}
        beyond: list[tuple[int, int]] = []
        for atoms_, o in leaves:
            a = ruling_amounts(d, o, self.m)
            total = sum(a.values())
            if total == 0:
                c = "none"
            elif self.reach is not None and total * lower > self.reach:
                c, _ = "beyond", beyond.append((total, a["fees"]))
            else:
                c = f"amt:{total}:{a['fees']}"
            classes.setdefault(c, []).append(atoms_)
        if "beyond" in classes:
            lo = min(beyond)
            classes[f"beyond:{lo[0]}:{lo[1]}"] = classes.pop("beyond")
        self.beyond_range = (min(beyond)[0], max(beyond)[0]) if beyond else None
        return classes


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
        facts: dict = {"components": [{"component": c.label, "status": c.status,
                                       "amount": usd(c.amount_cents) if c.amount_cents is not None else
                                       ("computed by statute" if c.statutory else "unknown"),
                                       **({"remittitur_scenario": usd(c.remittitur_cents)} if c.remittitur_cents else {})}
                                      for c in d.components]}
        if n.question_id in self.no_cash:
            return facts
        rows = self.facts.get(n.key, [])
        if not rows:
            return {**facts, "arises": "not computed"}
        day = np.concatenate([r[0] for r in rows])
        inside = day < self.days
        facts["share_of_trajectories_where_it_arises"] = round(float(inside.mean()), 3)
        if not inside.any():
            return facts
        cash, owed, coll = (np.concatenate([r[i] for r in rows])[inside] for i in (1, 2, 3))
        facts["decision_date"] = {"p5": self._date(np.quantile(day[inside], 0.05)),
                                  "p50": self._date(np.quantile(day[inside], 0.5)),
                                  "p95": self._date(np.quantile(day[inside], 0.95))}
        facts["available_cash_at_decision"] = {"p5": usd(int(np.quantile(cash, 0.05))),
                                               "p50": usd(int(np.quantile(cash, 0.5)))}
        facts["amount_owed_at_decision"] = {"p50": usd(int(np.quantile(owed, 0.5))),
                                            "max": usd(int(owed.max()))}
        if reg.get("node") in ("stay_motion", "stay_approved"):
            facts["bond_collateral_required"] = usd(int(np.quantile(coll, 0.5)))
        if any(f.status != "superseded" for f in d.financing):
            f = next(f for f in d.financing if f.status != "superseded")
            facts["notes"] = {"principal": usd(f.principal_cents),
                              "judgment_default": (f"final money judgments above {usd(f.judgment_default_threshold_cents)}"
                                                   f" unpaid or unstayed for {f.judgment_default_days} days, after "
                                                   f"notice" if f.judgment_default_days else "none")}
        return facts

    @property
    def no_cash(self) -> set[str]:
        return set(load_registry().get("evidence_routing", {}).get("no_cash", []))

    def _routed(self, qid: str) -> set[str]:
        routes = load_registry().get("evidence_routing", {}).get("routes", {})
        return {f for f, qs in routes.items() if qid in qs}

    def _evidence(self, d: DisputeInstance, qid: str) -> tuple[list[dict], tuple[str, ...]]:
        factors = self._routed(qid)
        chosen = [r.finding_id for r in d.readings
                  if any((r.bears_on.get(f) or 0) >= ESTABLISHED for f in factors)
                  or any((r.events.get(e) or 0) >= ESTABLISHED for e in r.events)]
        if not chosen:
            chosen = [r.finding_id for r in d.readings] or list(d.finding_ids)
        date_of = {r.finding_id: r.source_date for r in d.readings}
        dated = sorted(dict.fromkeys(chosen), key=lambda fid: date_of.get(fid, ""), reverse=True)[:MAX_PASSAGES]
        return [self.hydrate(self.findings[fid]) for fid in dated if fid in self.findings], tuple(dated)

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
        evidence, fids = self._evidence(d, n.question_id)
        readings = self._readings(d, n.question_id)
        ctx = [c for c in n.context.split("|") if c]
        state = {"case": {"as_of": fmt(self.review), "analysis_period_ends": fmt(self.horizon),
                          "borrower": self.borrower, "counterparty": d.counterparty,
                          "obligation": f"{self.m['natures'].get(d.nature, d.nature)}, {d.order_reference}"},
                 "question": {"actor": s["actor"], "decision": s["decision"], "branches": list(n.branches),
                              "timing": s["timing"], "context": ctx},
                 "standard": [self.m["rules"][r]["citation"] if r in self.m["rules"] else r for r in s["standard"]],
                 "record_items": s["record_items"], "path_facts": self.path_facts(n, d),
                 "assumptions": list(n.assumptions), "evidence": evidence, "readings": readings}
        return state, fids, readings

    async def judge(self, judge: ForecastJudge) -> dict[str, Judgment]:
        async def one(n: Node) -> Judgment:
            st, fids, readings = self.state(n)
            o = await judge.forecast(n.question_id, st, (n.instance_id, *fids), n.branches)
            if n.branches == ("yes", "no"):
                if o.noul_value is None:
                    raise RuntimeError(f"Jev returned no probability for {n.key}")
                dist = {"yes": float(o.noul_value), "no": 1.0 - float(o.noul_value)}
            else:
                probs = {k: float(v) for k, v in (o.probabilities or {}).items() if k in n.branches}
                total = sum(probs.values())
                if total <= 0:
                    raise RuntimeError(f"Jev returned no distribution for {n.key}")
                dist = {k: probs.get(k, 0.0) / total for k in n.branches}
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

    def add(self, step, edge, **kw) -> _S:
        return _S(self.steps + (step,), self.edges + ((edge,) if edge else ()),
                  **{**{k: getattr(self, k) for k in ("cls", "stayed", "appealed", "early", "a4")}, **kw})


def _label(c: str) -> str:
    return c.split(":")[0] + (c.split(":")[1] if c.startswith("amt:") else "")


class _Walk:
    def __init__(self, fc: Forecaster, d: DisputeInstance) -> None:
        self.fc, self.d, self.out = fc, d, []
        self.fin = next((f for f in d.financing if f.status != "superseded"), None)
        self.N = fc.days
        if self.fin is not None and fc.reach is not None and self.fin.principal_cents <= fc.reach:
            raise NotImplementedError("Paying the notes is arithmetically possible on some trajectory; the chains remove "
                                      "that branch only when the principal exceeds cash on every trajectory")

    # helpers
    def node(self, name, *ctx, assumptions=(), branches=None):
        return self.fc.node(self.d, name, *ctx, assumptions=assumptions, branches=branches)

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
        if not self.arises(s, ("settle", interval, "no")):
            return then_no(s)
        a3 = self.node("settlement_offer", interval, s.cls)
        q4 = self.node("settlement_accept", interval, s.cls, assumptions=("the debtor offers terms within its bound",))
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
        a1 = self.node("stay_motion", "I1", s.cls, assumptions=("the creditor executes before the ruling",))
        j8 = self.node("stay_approved", "I1", s.cls, assumptions=("the debtor moves for a stay",))
        self.binary(s, "stay", "I1", [[(a1, "yes"), (j8, "yes")]], (a1, j8),
                    lambda y: self.ripe_i1(replace(y, stayed=True)), self.a4_i1)

    def a4_i1(self, s: _S) -> None:
        self.a4(s, "I1", self.j9_i1, lambda y: self.emit(y, "petition"))

    def a4(self, s: _S, phase: str, then, on_file) -> None:
        probe = ("debtor_response", phase, "seek_sale_or_financing")
        pay = self.fc.pay_possible(self.d, s.steps, probe)
        branches = (("pay",) if pay else ()) + ("seek_sale_or_financing", "file", "neither")
        k = self.node("debtor_response", phase, s.cls, "pay" if pay else "nopay",
                      "after_seek" if s.a4 == "seek" else "first",
                      assumptions=("the judgment is enforceable, unstayed and unpaid",), branches=branches)
        for b in branches:
            y = self.take(s, ("debtor_response", phase, b), (k, b), (k,),
                          a4="seek" if b.startswith("seek") else "closed")
            if b == "pay":
                self.tail(y, "paid")
            elif b == "file":
                on_file(y)
            else:
                then(y)

    def j9_i1(self, s: _S) -> None:
        k = self.node("registration_early", "I1", assumptions=("the creditor executes before finality",))
        self.ripe_i1(self.take(s, ("registration_early", "I1", "yes"), (k, "yes"), (k,), early=True))
        self.ripe_i1(self.take(s, ("registration_early", "I1", "no"), (k, "no"), (k,)))

    def notes_petition(self, s: _S, phase: str, then) -> None:
        """The judgment default: the holders give notice and accelerate (H1), then the issuer files (A5) or else the
        holders file (H3). Paying the notes is removed by arithmetic."""
        f = self.fin
        if f is None or not f.judgment_default_days or not self.arises(s, ("judgment_default", phase, "no")):
            return then(s)
        h1 = self.node("holders_act_judgment", phase, s.cls)
        a5 = self.node("petition_on_notes", f"judgment_{phase}", assumptions=("the holders accelerate the notes",))
        h3 = self.node("holders_involuntary", f"judgment_{phase}",
                       assumptions=("the notes are accelerated and unpaid", "the issuer does not file"))
        parts = [[(h1, "yes"), (a5, "yes")], [(h1, "yes"), (a5, "no"), (h3, "yes")]]
        k = composite(parts)
        y = self.take(s, ("judgment_default", phase, "yes"), (k, "yes"), (h1, a5, h3))
        tr = self.fc.trace(self.d, y.steps)
        if (tr.events.petition >= 0).all():  # a petition on every trajectory: only an earlier one (tau) can matter
            tau = self.fc.trace(self.d, y.steps + (("cash_floor", "", "no"),)).day[-1]
            self.floor(y, "petition", bool((tau < tr.events.petition).any()))
        else:
            then(y)
        then(self.take(s, ("judgment_default", phase, "no"), (k, "no"), (h1, a5, h3)))

    def ripe_i1(self, s: _S) -> None:
        self.notes_petition(s, "I1", self.ruling)


    # the ruling and after
    def ruling(self, s: _S) -> None:
        for c, parts in self.fc.ruling_classes(self.d).items():
            y = s.add(("ruling", "", c), (composite(parts), "yes"), cls=_label(c))
            if c == "none":
                self.tail(y, "vacated")
            else:
                self.post(y)

    def post(self, s: _S) -> None:
        self.settle(s, "I2", self.appeal)

    def appeal(self, s: _S) -> None:
        if s.appealed or not self.arises(s, ("appeal", "", "no")):
            return self.stay_post(s)
        k = self.node("appeal", s.cls, assumptions=("a money award survives the ruling",))
        self.stay_post(self.take(s, ("appeal", "", "yes"), (k, "yes"), (k,), appealed=True))
        self.stay_post(self.take(s, ("appeal", "", "no"), (k, "no"), (k,)))

    def stay_post(self, s: _S) -> None:
        if s.stayed:
            return self.settle(s, "I4", lambda y: self.tail(y, "stayed"))
        if not self.arises(s, ("stay", "post", "no")):
            return self.i3(s)
        a1 = self.node("stay_motion", "post", s.cls, assumptions=("the final judgment is entered",))
        j8 = self.node("stay_approved", "post", s.cls, assumptions=("the debtor moves for a stay",))
        self.binary(s, "stay", "post", [[(a1, "yes"), (j8, "yes")]], (a1, j8),
                    lambda y: self.settle(replace(y, stayed=True), "I4", lambda z: self.tail(z, "stayed")), self.i3)

    def i3(self, s: _S) -> None:
        self.settle(s, "I3", self.a4_post)

    def a4_post(self, s: _S) -> None:
        if s.a4 == "closed" or not self.arises(s, ("debtor_response", "post", "neither")):
            return self.enforce(s)
        self.a4(s, "post", self.enforce, lambda y: self.tail(y, "petition"))

    def enforce(self, s: _S) -> None:
        if not self.arises(s, ("enforce", "post", "none")):
            return self.ripe_post(s)
        q3 = self.node("enforce_after_final", s.cls, "appealed" if s.appealed else "final",
                       assumptions=("the judgment is enforceable, unstayed and unpaid after the ruling",))
        if s.appealed and not s.early:
            j9 = self.node("registration_early", "post", assumptions=("the creditor enforces before finality",))
            levy, none, keys = [[(q3, "yes"), (j9, "yes")]], [[(q3, "no")], [(q3, "yes"), (j9, "no")]], (q3, j9)
        else:
            levy, none, keys = [[(q3, "yes")]], [[(q3, "no")]], (q3,)
        self.ripe_post(self.take(s, ("enforce", "post", "levy"), (composite(levy), "yes"), keys))
        self.ripe_post(self.take(s, ("enforce", "post", "none"), (composite(none), "yes"), keys))

    def ripe_post(self, s: _S) -> None:
        self.notes_petition(s, "post", lambda y: self.tail(y, "unresolved"))

    # the listing chain, then the cash floor
    def tail(self, s: _S, outcome: str) -> None:
        floor = self._floor(s)
        f = self.fin
        if f is None or f.listing_deadline is None:
            return self.floor(s, outcome, floor)
        dates = _listing_dates(self.fc, self.d)
        if min(dates["delisted_panel"], dates["delisted_suspension"]) >= self.N:
            return self.floor(s, outcome, floor)  # the listing chain closes after the horizon
        a7 = self.node("reverse_split_board")
        st1 = self.node("split_approved", assumptions=("the board calls the vote in time",))
        a8 = self.node("nasdaq_hearing", assumptions=("the stock is not compliant on the deadline",))
        n1 = self.node("panel_exception", assumptions=("the issuer requests a hearing",))
        not_ok = [[(a7, "no")], [(a7, "yes"), (st1, "no")]]
        classes = {"listed": [[(a7, "yes"), (st1, "yes")]] + [c + [(a8, "yes"), (n1, "yes")] for c in not_ok],
                   "delisted_panel": [c + [(a8, "yes"), (n1, "no")] for c in not_ok],
                   "delisted_suspension": [c + [(a8, "no")] for c in not_ok]}
        for c in ("delisted_panel", "delisted_suspension"):
            if dates[c] >= self.N:  # delisted only after the horizon: listed throughout it (window closed)
                classes["listed"] += classes.pop(c)
        keys = (a7, st1, a8, n1)
        for c, parts in classes.items():
            y = self.take(s, ("listing", "", c), (composite(parts), "yes"), keys)
            if c == "listed":
                self.floor(y, outcome, floor)
            else:
                self.delisting_notes(y, c, dates[c], outcome, floor)

    def delisting_notes(self, s: _S, dc: str, delist: int, outcome: str, floor: bool) -> None:
        h2 = self.node("holders_act_delisting", dc, assumptions=("the stock is not listed on an Eligible Market",))
        a5 = self.node("petition_on_notes", f"delisting_{dc}", assumptions=("the holders accelerate the notes",))
        h3 = self.node("holders_involuntary", f"delisting_{dc}",
                       assumptions=("the notes are accelerated and unpaid", "the issuer does not file"))
        classes = {"petition_delist": [[(h2, "accelerate"), (a5, "yes")], [(h2, "accelerate"), (a5, "no"), (h3, "yes")]]}
        none = [[(h2, "neither")], [(h2, "accelerate"), (a5, "no"), (h3, "no")]]
        keys = [h2, a5, h3]
        chain = Chain_(self.fc, self.d)
        if chain.repurchase_day(delist) < self.N:
            a5r = self.node("petition_on_notes", f"repurchase_{dc}", assumptions=("the repurchase falls due unpaid",))
            h3r = self.node("holders_involuntary", f"repurchase_{dc}",
                            assumptions=("the repurchase is unpaid", "the issuer does not file"))
            classes["petition_repurchase"] = [[(h2, "repurchase_only"), (a5r, "yes")],
                                              [(h2, "repurchase_only"), (a5r, "no"), (h3r, "yes")]]
            none.append([(h2, "repurchase_only"), (a5r, "no"), (h3r, "no")])
            keys += [a5r, h3r]
        else:  # the repurchase date falls after the horizon: requiring it moves nothing inside it
            none.append([(h2, "repurchase_only")])
        classes["none"] = none
        for c, parts in classes.items():
            y = self.take(s, ("delisting_notes", dc, c), (composite(parts), "yes"), keys)
            self.floor(y, outcome, floor)

    def _floor(self, s: _S) -> bool:
        """Whether tau falls inside the horizon on some trajectory of the path (the listing chain moves no cash)."""
        tr = self.fc.trace(self.d, s.steps + (("cash_floor", "", "no"),))
        return bool((tr.day[-1] < self.N).any())

    def floor(self, s: _S, outcome: str, arises: bool) -> None:
        if not arises:
            return self.emit(s, outcome)
        levied = any(st[0] in ("registration_early", "enforce") and st[2] in ("yes", "levy") for st in s.steps)
        k = self.node("petition_cash_floor", s.cls, "levied" if levied else "unlevied")
        self.fc.record((k,), self.fc.trace(self.d, s.steps + (("cash_floor", "", "no"),)))
        self.emit(s.add(("cash_floor", "", "yes"), (k, "yes")), "petition")
        self.emit(s.add(("cash_floor", "", "no"), (k, "no")), outcome)

    def emit(self, s: _S, outcome: str) -> None:
        self.out.append(DisputePath(instance_id=self.d.instance_id, steps=s.steps, outcome=outcome, edges=s.edges))


def Chain_(fc: Forecaster, d: DisputeInstance):
    from app.analysis.events import Chain

    return Chain(d, fc.setup, fc.m, fc.draws, fc.sens)


def _listing_dates(fc: Forecaster, d: DisputeInstance) -> dict[str, int]:
    return Chain_(fc, d).listing_dates()
