"""Traverse the dispute model for one instantiated dispute: place the stage, judge transitions, refine by value of
information, and compose the paths with rule-derived cash.

Jev answers atomic questions through a `judge` (host-owned profiles in jev_profiles.py). Code owns everything else:
which question is asked when, the refinement trigger, the dates and amounts (rules.py), the path weights (products of
conditional weights) and pruning. Without a judge (the agent-only arm) the agent's stage is used and every path is
shown conditionally, unweighted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from app.disputes.rules import Window, amount_range, cash_for, load_model, next_window
from app.domain.investigation import (
    AtomicFinding,
    BranchCash,
    DisputeInstance,
    DisputePath,
    EvidenceRequest,
    FactorResult,
    SemanticObservation,
    TransitionJudgment,
)

NONE_FITS = "none_fits"
NO_FACTOR = "none"
DATED_STAGES = ("judgment_entered", "appeal_pending", "enforcement")


class DisputeJudge(Protocol):
    async def stage(self, dispute: dict, record: list[dict], options: dict[str, str],
                    subject_ids: tuple[str, ...]) -> SemanticObservation: ...

    async def route(self, dispute: dict, finding: dict, options: dict[str, str],
                    subject_ids: tuple[str, ...]) -> SemanticObservation: ...

    async def level(self, dispute: dict, finding: dict, factor: dict, rubric: list[str],
                    subject_ids: tuple[str, ...]) -> SemanticObservation: ...

    async def present(self, dispute: dict, finding: dict, factor: dict,
                      subject_ids: tuple[str, ...]) -> SemanticObservation: ...

    async def transition(self, dispute: dict, record: list[dict], stage: dict, premise: list[str],
                         factor_results: list[dict], options: dict[str, str],
                         subject_ids: tuple[str, ...]) -> SemanticObservation: ...


def to_bps(probabilities: dict[str, float] | None, keys: list[str]) -> dict[str, int]:
    """A Choice distribution over `keys` as basis points summing to exactly 10,000 (empty if there is none)."""
    probs = {k: Decimal(str((probabilities or {}).get(k, 0))) for k in keys}
    total = sum(probs.values())
    if total <= 0:
        return {}
    raw = {k: int((v / total * 10_000).to_integral_value()) for k, v in probs.items()}
    raw[max(raw, key=raw.get)] += 10_000 - sum(raw.values())
    return raw


def normalise(weights: list[int]) -> list[int]:
    total = sum(weights)
    out = [int((Decimal(w) / total * 10_000).to_integral_value()) for w in weights]
    out[out.index(max(out))] += 10_000 - sum(out)
    return out


class Evaluator:
    def __init__(self, draft: DisputeInstance, findings: list[AtomicFinding], *, judge: DisputeJudge | None,
                 borrower: str, source_dates: dict[str, str], review: date, horizon: date, request_cents: int,
                 agent_stage: str | None = None, model: dict | None = None) -> None:
        self.m = model or load_model()
        self.d, self.findings, self.judge = draft, findings, judge
        self.review, self.horizon, self.request_cents, self.agent_stage = review, horizon, request_cents, agent_stage
        debtor, creditor = (borrower, draft.counterparty) if draft.borrower_role == "debtor" else (draft.counterparty, borrower)
        self.dispute = {"title": draft.title, "borrower": borrower, "judgment_debtor": debtor,
                        "judgment_creditor": creditor, "review_date": review.isoformat(),
                        "financing_horizon_ends": horizon.isoformat(),
                        "amount": "set by code from the record; not for judgment"}
        self.record = [self._finding(f, source_dates) for f in findings]
        self.subject = (draft.instance_id, *draft.finding_ids)
        self.obs: list[str] = []
        self.judgments: list[TransitionJudgment] = []
        self.routes: dict[str, set[str]] | None = None
        self.factor_cache: dict[str, FactorResult] = {}
        self.requests: dict[str, EvidenceRequest] = {}

    @staticmethod
    def _finding(f: AtomicFinding, source_dates: dict[str, str]) -> dict:
        return {"finding_id": f.finding_id, "proposition": f.proposition, "quotes": [s.quote for s in f.spans],
                "source_dates": sorted({source_dates.get(s.source_id, "") for s in f.spans})}

    def _record(self, o: SemanticObservation) -> SemanticObservation:
        self.obs.append(o.observation_id)
        return o

    # --- stage ------------------------------------------------------------------------------------

    async def place(self) -> tuple[str | None, dict[str, int]]:
        stages = {k: v for k, v in self.m["stages"].items() if not v["terminal"]}
        if self.judge is None:
            return (self.agent_stage if self.agent_stage in stages else None), {}
        options = {k: f"{v['label']}: {v['description']}" for k, v in stages.items()}
        options[NONE_FITS] = "None of these stages fits the record."
        o = self._record(await self.judge.stage(self.dispute, self.record, options, self.subject))
        weights = to_bps(o.probabilities, list(options))
        top = max(weights, key=weights.get) if weights else None
        return (None if top in (None, NONE_FITS) else top), weights

    # --- factors (refinement) ---------------------------------------------------------------------

    async def _route_all(self) -> dict[str, set[str]]:
        """factor_routing, once per finding: every factor whose probability meets the routing threshold."""
        if self.routes is None:
            threshold = self.m["refinement"]["route_if_probability_at_least"]
            options = {k: f"{f['label']}: {f['question']}" for k, f in self.m["factors"].items()}
            options[NO_FACTOR] = "The finding bears on none of these factors."
            self.routes = {k: set() for k in self.m["factors"]}
            for f, rec in zip(self.findings, self.record, strict=True):
                o = self._record(await self.judge.route(self.dispute, rec, options, (f.finding_id, *self.subject)))
                for k, p in (o.probabilities or {}).items():
                    if k in self.routes and p >= threshold:
                        self.routes[k].add(f.finding_id)
        return self.routes

    async def factor(self, factor_id: str) -> FactorResult:
        if factor_id in self.factor_cache:
            return self.factor_cache[factor_id]
        spec = self.m["factors"][factor_id]
        routed = [(f, r) for f, r in zip(self.findings, self.record, strict=True)
                  if f.finding_id in (await self._route_all())[factor_id]]
        levels, fids, oids = [], [], []
        for f, rec in routed:
            sid = (f.finding_id, self.d.instance_id)
            factor = {"factor_id": factor_id, "label": spec["label"], "question": spec["question"]}
            if spec["kind"] == "graded":
                o = self._record(await self.judge.level(self.dispute, rec, factor, spec["levels"], sid))
                if o.answer is not None:
                    levels.append(int(o.answer))
            else:
                o = self._record(await self.judge.present(self.dispute, rec, factor, sid))
                if o.answer is not None:
                    levels.append(1 if o.answer else 0)
            fids.append(f.finding_id)
            oids.append(o.observation_id)
        level = max(levels) if levels else None  # the most advanced level any routed finding establishes
        if level is None:
            label = "unknown"
        elif spec["kind"] == "graded":
            label = spec["levels"][level]
        else:
            label = "present" if level else "not established"
        result = FactorResult(factor_id=factor_id, label=spec["label"], kind=spec["kind"], level=level,
                              level_label=label, finding_ids=tuple(fids), observation_ids=tuple(oids))
        self.factor_cache[factor_id] = result
        return result

    # --- transitions ------------------------------------------------------------------------------

    def _outcomes(self, stage: str, at: Window) -> set[int]:
        """Net cash (high amounts; inflows positive, outflows and locks negative) of every path from this stage within
        the horizon."""
        if self.m["stages"][stage]["terminal"]:
            return {0}
        return {o for t in self.m["transitions"][stage] for o in self._option_outcomes(t, at)}

    def _option_outcomes(self, t: dict, at: Window) -> set[int]:
        cash = cash_for(self.m, t["rule"], self.d.borrower_role, self.d.amount, at, self.review, self.horizon, (),
                        self.d.amount_includes_interest)
        here = sum(amount_range(c.amount)[1] * (1 if c.kind == "inflow" else -1) for c in cash)
        return {here + o for o in self._outcomes(t["to"], next_window(self.m, t["rule"], t["to"], at, self.review))}

    async def judge_transition(self, stage: str, at: Window, premise: list[dict]) -> TransitionJudgment:
        options_spec = self.m["transitions"][stage]
        keys = [t["transition_id"] for t in options_spec]
        if self.judge is None:
            j = TransitionJudgment(stage=stage, premise=tuple(p["transition_id"] for p in premise))
            self.judgments.append(j)
            return j
        ref = self.m["refinement"]
        options = {t["transition_id"]: f"{t['label']}: {t['description']}" for t in options_spec}
        stage_state = {"stage": stage, **{k: v for k, v in self.m["stages"][stage].items() if k != "terminal"}}
        premise_text = [p["label"] for p in premise]
        o = self._record(await self.judge.transition(self.dispute, self.record, stage_state, premise_text, [], options,
                                                     self.subject))
        weights, oids = to_bps(o.probabilities, keys), [o.observation_id]
        if not weights:
            raise ValueError(f"Jev returned no distribution over the transitions from {stage}")
        outcomes = self._outcomes(stage, at)  # decision relevance: how far apart this stage's outcomes are
        relevant = max(outcomes) - min(outcomes) >= self.request_cents * ref[
            "decision_relevant_if_cash_spread_at_least_share_of_request_bps"] // 10_000
        uncertain = not weights or max(weights.values()) < ref["uncertain_if_top_below_bps"]
        refined, factor_ids, initial = False, tuple(dict.fromkeys(f for t in options_spec for f in t["factors"])), {}
        if uncertain and relevant:
            results = [await self.factor(f) for f in factor_ids]
            known = [{"factor": r.label, "level": r.level_label, "finding_ids": list(r.finding_ids)}
                     for r in results if r.level is not None]
            o = self._record(await self.judge.transition(self.dispute, self.record, stage_state, premise_text, known,
                                                         options, self.subject))
            initial = weights
            weights, refined = to_bps(o.probabilities, keys) or weights, True
            oids.append(o.observation_id)
            uncertain = not weights or max(weights.values()) < ref["uncertain_if_top_below_bps"]
            if uncertain:
                for r in results:
                    if r.level is None and r.factor_id not in self.requests:
                        self.requests[r.factor_id] = EvidenceRequest(factor_id=r.factor_id,
                                                                     **self.m["factors"][r.factor_id]["evidence_request"])
        j = TransitionJudgment(stage=stage, premise=tuple(p["transition_id"] for p in premise), weights_bps=weights,
                               initial_weights_bps=initial, uncertain=uncertain, decision_relevant=relevant, refined=refined,
                               factor_ids=factor_ids if refined else (), observation_ids=tuple(oids))
        self.judgments.append(j)
        return j

    async def expand(self, stage: str, at: Window, premise: list[dict], weight: Decimal | None,
                     cash: tuple[BranchCash, ...], out: list[tuple], pruned: list[Decimal]) -> None:
        j = await self.judge_transition(stage, at, premise)
        floor = Decimal(self.m["refinement"]["min_path_weight_bps"]) / 10_000
        for t in self.m["transitions"][stage]:
            w = None if weight is None or not j.weights_bps else weight * Decimal(j.weights_bps.get(t["transition_id"], 0)) / 10_000
            if w is not None and w < floor:
                pruned.append(w)
                continue
            step = cash_for(self.m, t["rule"], self.d.borrower_role, self.d.amount, at, self.review, self.horizon,
                            self.d.finding_ids, self.d.amount_includes_interest)
            path = [*premise, t]
            if self.m["stages"][t["to"]]["terminal"]:
                out.append((path, t["to"], w, cash + tuple(step)))
            else:
                await self.expand(t["to"], next_window(self.m, t["rule"], t["to"], at, self.review), path, w,
                                  cash + tuple(step), out, pruned)

    async def run(self) -> DisputeInstance:
        stage, stage_weights = await self.place()
        update: dict = {"stage": stage, "stage_weights_bps": stage_weights, "model_id": self.m["model_id"],
                        "model_version": self.m["model_version"]}
        if stage is None:
            req = EvidenceRequest(factor_id="stage", action=f"Obtain the docket for {self.d.title} to place it on the "
                                                            "dispute model.",
                                  if_satisfied="The dispute's paths and their cash enter the event-adjusted view.",
                                  if_not="The event-adjusted recommendation stays incomplete for this dispute.")
            return self.d.model_copy(update={**update, "status": "outside_model", "evidence_requests": (req,),
                                             "observation_ids": tuple(self.obs)})
        if stage in DATED_STAGES and self.d.judgment_date is None:
            req = EvidenceRequest(factor_id="judgment_date", action="Confirm the judgment's entry date from the docket.",
                                  if_satisfied="The dispute's paths are dated from it.",
                                  if_not="The event-adjusted recommendation stays incomplete for this dispute.")
            return self.d.model_copy(update={**update, "status": "outside_model", "evidence_requests": (req,),
                                             "observation_ids": tuple(self.obs)})
        at = Window(self.d.judgment_date, self.d.judgment_date) if stage in DATED_STAGES else Window(self.review, self.review)
        out: list[tuple] = []
        pruned: list[Decimal] = []
        weighted = self.judge is not None
        await self.expand(stage, at, [], Decimal(1) if weighted else None, (), out, pruned)
        weights = normalise([int((w * 10_000).to_integral_value()) for _, _, w, _ in out]) if weighted else [None] * len(out)
        paths = tuple(DisputePath(path_id=f"{self.d.instance_id}_p{i}", transitions=tuple(t["transition_id"] for t in p),
                                  labels=tuple(t["label"] for t in p), terminal=term, weight_bps=w, cash=c)
                      for i, ((p, term, _, c), w) in enumerate(zip(out, weights, strict=True), 1))
        return self.d.model_copy(update={
            **update, "status": "evaluated" if weighted else "not_judged", "paths": paths,
            "transitions": tuple(self.judgments), "factors": tuple(self.factor_cache.values()),
            "pruned_weight_bps": int((sum(pruned, Decimal(0)) * 10_000).to_integral_value()),
            "evidence_requests": tuple(self.requests.values()), "observation_ids": tuple(self.obs)})
