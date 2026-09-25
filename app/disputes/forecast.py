"""Conditional forecasting: the event tree of each interpreted dispute, Jev's probability at every node, and the paths.

The tree is bounded by cash: settlement, the amount being fixed, an appeal, a secured stay and its form, voluntary
payment, collection by enforcement, and settlement during a secured appeal. At each node Jev is asked for the
probability of one defined event, under the parent assumptions of that node (in words, with dates set by code), given
the relevant hydrated passages and the present-state factor distributions. Only an established fact changes the tree
(an appeal waiver covering the obligation removes the appeal branch). Every structurally feasible path is enumerated,
whatever its probability.

Dependence: a later dispute with the same counterparty is asked once per outcome class of the earlier dispute (whether
the counterparty receives or pays cash in it within the horizon); joint probability = P(earlier path) x P(later | class).
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from app.disputes.interpret import ESTABLISHED
from app.disputes.rules import load_model
from app.domain.investigation import AtomicFinding, DisputeInstance, SemanticObservation
from app.domain.values import usd

MAX_PASSAGES = 6
PAYS_OUT = {"settled", "paid", "collected", "settled_during_appeal"}  # outcomes where the payer pays within the horizon


class ForecastJudge(Protocol):
    async def forecast(self, question_id: str, state: dict, subject_ids: tuple[str, ...]) -> SemanticObservation: ...


@dataclass(frozen=True)
class Node:
    key: str  # instance:node[|context][|cls=...]
    instance_id: str
    node: str
    context: str
    cls: str
    question_id: str
    event: str
    assumptions: tuple[str, ...]
    window: str
    branches: tuple[str, ...]  # ("yes", "no") or the security options


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


@dataclass(frozen=True)
class DisputePath:
    """One path through one dispute's tree: the steps taken (node, context, branch), its outcome, and the edges that
    carry its probability (node key, branch)."""
    instance_id: str
    steps: tuple[tuple[str, str, str], ...]
    outcome: str
    edges: tuple[tuple[str, str], ...]
    cls: str = ""


def fmt(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


class Forecaster:
    def __init__(self, disputes: list[DisputeInstance], findings: dict[str, AtomicFinding], *, borrower: str,
                 review: date, horizon: date, hydrate: Callable[[AtomicFinding], dict],
                 model: dict | None = None) -> None:
        self.m = model or load_model()
        self.disputes = [d for d in disputes if d.status == "interpreted"]
        self.findings, self.borrower, self.review, self.horizon, self.hydrate = findings, borrower, review, horizon, hydrate
        self.nodes: dict[str, Node] = {}

    # --- order and dependence ---------------------------------------------------------------------

    def ordered(self) -> list[tuple[DisputeInstance, DisputeInstance | None]]:
        """Each dispute with the earlier same-counterparty dispute it is conditioned on (None if independent).
        The borrower-as-payer dispute comes first: its outcome changes the counterparty's position."""
        ds = sorted(self.disputes, key=lambda d: (d.borrower_role != "debtor", d.instance_id))
        out, first_by_cp = [], {}
        for d in ds:
            parent = first_by_cp.get(d.counterparty)
            out.append((d, parent))
            first_by_cp.setdefault(d.counterparty, d)
        return out

    @staticmethod
    def path_class(d: DisputeInstance, outcome: str) -> str:
        if outcome not in PAYS_OUT:
            return "no_cash"
        return "counterparty_receives" if d.borrower_role == "debtor" else "counterparty_pays"

    def class_assumption(self, parent: DisputeInstance, cls: str) -> str:
        cp = parent.counterparty
        return {"counterparty_receives": f"In the other dispute between these parties ({parent.order_reference}), "
                                         f"{cp} receives a payment from {self.borrower} during the analysis period.",
                "counterparty_pays": f"In the other dispute between these parties ({parent.order_reference}), "
                                     f"{cp} pays {self.borrower} during the analysis period.",
                "no_cash": f"In the other dispute between these parties ({parent.order_reference}), no payment passes "
                           f"between them during the analysis period."}[cls]

    # --- the tree ---------------------------------------------------------------------------------

    def _windows(self, d: DisputeInstance) -> dict[str, str]:
        r = self.m["rules"]
        stay, notice = r["automatic_stay_days"]["value"], r["appeal_notice_days"]["value"]
        vol, enf, settle = (r["voluntary_payment_days_after_stay"]["value"], r["enforcement_period_days"]["value"],
                            r["settlement_window_days"]["value"])
        first, h = self.review + timedelta(days=1), self.horizon

        def span(a: date, b: date) -> str:
            return f"from {fmt(max(a, first))} to {fmt(min(b, h))}" if a <= h else "after the analysis period"

        w = {"settle_before_ruling": span(first, self.review + timedelta(days=settle)),
             "amount_fixed": span(first, h), "settle_during_appeal": f"from the stay to {fmt(h)}"}
        if d.stage == "amount_pending" or d.judgment_date is None:
            j = "the day the court fixes the amount"
            w.update({"settle_after_judgment": f"within {notice} days after {j}",
                      "appeal": f"within {notice} days after {j}", "secured_stay": f"within {stay} days after {j}",
                      "voluntary_payment": f"between {stay} and {stay + vol} days after {j}",
                      "enforcement": f"between {stay} and {stay + enf} days after {j}",
                      "security_form": "when the stay is secured"})
        else:
            dj = d.judgment_date
            w.update({"settle_after_judgment": span(dj, dj + timedelta(days=notice)),
                      "appeal": span(dj, dj + timedelta(days=notice)), "secured_stay": span(dj, dj + timedelta(days=stay)),
                      "voluntary_payment": span(dj + timedelta(days=stay), dj + timedelta(days=stay + vol)),
                      "enforcement": span(dj + timedelta(days=stay), dj + timedelta(days=stay + enf)),
                      "security_form": "when the stay is secured"})
        return w

    def _assume(self, d: DisputeInstance, prefix: list[tuple[str, str, str]]) -> list[str]:
        """Parent conditions, in words, from the steps taken so far."""
        payer = self.borrower if d.borrower_role == "debtor" else d.counterparty
        judged = (f"the judgment of {fmt(d.judgment_date)}" if d.judgment_date and d.stage != "amount_pending"
                  else "the court's ruling on the amount")
        text = {("settle_before_ruling", "no"): "The parties do not settle before the court rules on the amount.",
                ("amount_fixed", "yes"): f"The court fixes the amount between {fmt(self.review + timedelta(days=1))} "
                                         f"and {fmt(self.horizon)}.",
                ("settle_after_judgment", "no"): "The parties do not settle before the appeal deadline.",
                ("appeal", "yes"): f"{payer} files a notice of appeal against {judged}.",
                ("appeal", "no"): f"{payer} does not appeal {judged}.",
                ("secured_stay", "yes"): f"{payer} obtains a stay secured by a bond or other security.",
                ("secured_stay", "no"): f"{payer} does not obtain a secured stay.",
                ("voluntary_payment", "no"): f"{payer} does not pay voluntarily."}
        out = [text[(n, b)] for n, _, b in prefix if (n, b) in text]
        out += [f"{payer} secures the stay with {self.m['nodes']['security_form']['options'][b].split(' (')[0]}."
                for n, _, b in prefix if n == "security_form"]
        return out

    def _node(self, d: DisputeInstance, node: str, context: str, cls: str, prefix: list, cls_text: str) -> Node:
        spec = self.m["nodes"][node]
        key = f"{d.instance_id}:{node}" + (f"|{context}" if context else "") + (f"|cls={cls}" if cls else "")
        if key not in self.nodes:
            if node == "settle_during_appeal":  # one judgment whatever form secures the stay
                prefix = [s for s in prefix if s[0] != "security_form"]
            branches = tuple(spec["options"]) if "options" in spec else ("yes", "no")
            assumptions = tuple(self._assume(d, prefix) + ([cls_text] if cls_text else []))
            self.nodes[key] = Node(key=key, instance_id=d.instance_id, node=node, context=context, cls=cls,
                                   question_id=spec["question"], event=spec["event"], assumptions=assumptions,
                                   window=self._windows(d)[node], branches=branches)
        return self.nodes[key]

    def paths(self, d: DisputeInstance, cls: str = "", cls_text: str = "") -> list[DisputePath]:
        out: list[DisputePath] = []

        def emit(steps, outcome):
            out.append(DisputePath(instance_id=d.instance_id, steps=tuple(steps), outcome=outcome, cls=cls,
                                   edges=tuple((self._node(d, n, c, cls, [s for s in steps[:i]], cls_text).key, b)
                                               for i, (n, c, b) in enumerate(steps))))

        def payment(prefix, ctx):
            emit(prefix + [("voluntary_payment", ctx, "yes")], "paid")
            no = prefix + [("voluntary_payment", ctx, "no")]
            emit(no + [("enforcement", ctx, "yes")], "collected")
            emit(no + [("enforcement", ctx, "no")], "uncollected")

        def appeal_pending(prefix):
            emit(prefix + [("settle_during_appeal", "", "yes")], "settled_during_appeal")
            emit(prefix + [("settle_during_appeal", "", "no")], "appeal_pending")

        def judgment(prefix):
            emit(prefix + [("settle_after_judgment", "", "yes")], "settled")
            p = prefix + [("settle_after_judgment", "", "no")]
            if "appeal" in d.constraints:
                payment(p, "no_appeal")
                return
            a = p + [("appeal", "", "yes")]
            s = a + [("secured_stay", "", "yes")]
            if d.borrower_role == "debtor":
                for form in self.m["nodes"]["security_form"]["options"]:
                    appeal_pending(s + [("security_form", "", form)])
            else:
                appeal_pending(s)
            payment(a + [("secured_stay", "", "no")], "appeal_unsecured")
            payment(p + [("appeal", "", "no")], "no_appeal")

        if d.stage == "amount_pending":
            emit([("settle_before_ruling", "", "yes")], "settled")
            p = [("settle_before_ruling", "", "no")]
            emit(p + [("amount_fixed", "", "no")], "unresolved")
            judgment(p + [("amount_fixed", "", "yes")])
        elif d.stage == "judgment_entered":
            judgment([])
        elif d.stage == "appeal_pending":
            appeal_pending([])
        elif d.stage == "enforcement":
            emit([("enforcement", "", "yes")], "collected")
            emit([("enforcement", "", "no")], "uncollected")
        return out

    def all_paths(self) -> dict[str, dict[str, list[DisputePath]]]:
        """instance_id -> class ('' if independent) -> paths."""
        out: dict[str, dict[str, list[DisputePath]]] = {}
        for d, parent in self.ordered():
            if parent is None:
                out[d.instance_id] = {"": self.paths(d)}
            else:
                classes = sorted({self.path_class(parent, p.outcome) for p in out[parent.instance_id][""]})
                out[d.instance_id] = {c: self.paths(d, c, self.class_assumption(parent, c)) for c in classes}
        return out

    # --- forecast state and Jev -------------------------------------------------------------------

    def _case(self, d: DisputeInstance) -> dict:
        payer, payee = (self.borrower, d.counterparty) if d.borrower_role == "debtor" else (d.counterparty, self.borrower)
        amount = d.amount.value if d.amount.value is not None else d.amount.upper
        status = self.m["readings"]["amount_status_labels"].get(d.amount_status, "status not established")
        return {"as_of": fmt(self.review), "analysis_period_ends": fmt(self.horizon), "payer": payer, "payee": payee,
                "obligation": f"{self.m['natures'].get(d.nature, d.nature)}, {d.order_reference}",
                "amount": f"{usd(amount)} ({status})",
                "established": [f"{self.m['readings']['events'][k]} (passage dated {v.source_date})"
                                for k, v in d.established.items()]}

    def _evidence(self, d: DisputeInstance, node: str) -> tuple[list[dict], tuple[str, ...]]:
        factors = set(self.m["nodes"][node]["context_factors"])
        chosen = [r.finding_id for r in d.readings
                  if any((r.bears_on.get(f) or 0) >= ESTABLISHED for f in factors)
                  or any((r.events.get(e) or 0) >= ESTABLISHED for e in r.events)]
        if not chosen:
            chosen = [r.finding_id for r in d.readings]
        dated = sorted(chosen, key=lambda fid: next(r.source_date for r in d.readings if r.finding_id == fid),
                       reverse=True)[:MAX_PASSAGES]
        return [self.hydrate(self.findings[fid]) for fid in dated], tuple(dated)

    def _readings(self, d: DisputeInstance, node: str) -> dict:
        out = {}
        for f in d.factors:
            if f.factor_id not in self.m["nodes"][node]["context_factors"]:
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
        evidence, fids = self._evidence(d, n.node)
        readings = self._readings(d, n.node)
        return ({"case": self._case(d), "assumptions": list(n.assumptions),
                 "question": {"event": n.event, "window": n.window}, "evidence": evidence, "readings": readings},
                fids, readings)

    async def judge(self, judge: ForecastJudge) -> dict[str, Judgment]:
        async def one(n: Node) -> Judgment:
            st, fids, readings = self.state(n)
            o = await judge.forecast(n.question_id, st, (n.instance_id, *fids))
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
                            confidence=o.confidence, finding_ids=fids, readings=readings,
                            evidence=st["evidence"], observation_id=o.observation_id)

        results = await asyncio.gather(*(one(n) for n in self.nodes.values()))
        return {j.key: j for j in results}


def path_probability(edges: tuple[tuple[str, str], ...], dist: dict[str, dict[str, float]]) -> float:
    p = 1.0
    for key, branch in edges:
        p *= dist[key][branch]
    return p


def joint_paths(per: dict[str, dict[str, list[DisputePath]]], order: list[tuple[DisputeInstance, DisputeInstance | None]]
                ) -> list[tuple[DisputePath, ...]]:
    """Every combination of paths, the later same-counterparty dispute taken from the class its parent path implies."""
    combos: list[tuple[DisputePath, ...]] = [()]
    for d, parent in order:
        nxt = []
        for combo in combos:
            if parent is None:
                options = per[d.instance_id][""]
            else:
                ppath = next(p for p in combo if p.instance_id == parent.instance_id)
                options = per[d.instance_id][Forecaster.path_class(parent, ppath.outcome)]
            nxt += [combo + (p,) for p in options]
        combos = nxt
    return combos


def combo_probability(combo: tuple[DisputePath, ...], dist: dict[str, dict[str, float]]) -> float:
    return path_probability(tuple(itertools.chain.from_iterable(p.edges for p in combo)), dist)


def distributions(judgments: dict[str, Judgment], overrides: dict[str, dict[str, float]] | None = None
                  ) -> dict[str, dict[str, float]]:
    """Node key -> branch distribution, with any overrides applied (a Noul override sets yes and its complement)."""
    out = {k: dict(j.distribution) for k, j in judgments.items()}
    for k, v in (overrides or {}).items():
        if k in out:
            out[k] = dict(v)
    return out

