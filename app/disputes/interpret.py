"""Present-state interpretation of one live dispute: Jev reads each cited finding, hydrated with its surrounding
evidence, against one obligation; code derives the structural facts.

Jev returns full distributions (Choice, Noul, Score), all kept. Code derives only what is structural: who pays (the
readings must agree), the amount's status (the most advanced established), and which procedural events are
established (and so the stage). Only a fact that makes an event impossible changes structure: an event that has already
occurred sets the stage, and payment resolves the dispute. Everything else, a waiver included, is evidence: factor
distributions are aggregated by each factor's rule and passed to the forecasts as context; they never become
probabilities themselves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from app.disputes.rules import load_model
from app.domain.investigation import (
    AtomicFinding,
    Decisive,
    DisputeInstance,
    EvidenceRequest,
    FactorResult,
    FindingReading,
    SemanticObservation,
)

ESTABLISHED = 0.5  # code-owned: a Noul value at or above this establishes a present-state fact
DATED_STAGES = ("judgment_entered", "appeal_filed", "appeal_pending", "enforcement")
QUOTE_CHARS = 300


class InterpretationJudge(Protocol):
    async def read(self, obligation: dict, evidence: dict, direction: dict[str, str],
                   subject_ids: tuple[str, ...]) -> list[SemanticObservation]: ...

    async def relevance(self, obligation: dict, evidence: dict, subject_ids: tuple[str, ...],
                        factor_ids: list[str]) -> list[SemanticObservation]: ...

    async def level(self, obligation: dict, evidence: dict, factor: dict, rubric: list[str],
                    subject_ids: tuple[str, ...]) -> SemanticObservation: ...


def argmax(d: dict[str, float]) -> str | None:
    return max(d, key=lambda k: d[k]) if d else None


def expected_level(dist: dict[str, float], levels: list[str]) -> float:
    return sum(levels.index(k) * p for k, p in dist.items() if k in levels)


def obligation_state(inst: DisputeInstance, borrower: str, model: dict) -> dict:
    """What Jev is told about the obligation: a docket reference, its nature and the two parties, unordered.
    Nothing the agent wrote about who pays, the amount's status or anyone's intentions."""
    return {"reference": inst.order_reference, "nature": model["natures"].get(inst.nature, inst.nature),
            "parties": sorted([borrower, inst.counterparty])}


class Interpreter:
    def __init__(self, draft: DisputeInstance, findings: list[AtomicFinding], *, judge: InterpretationJudge | None,
                 borrower: str, sources: dict[str, tuple[str, str]], hydrate: Callable[[AtomicFinding], dict],
                 agent_reading: dict | None = None, model: dict | None = None) -> None:
        self.m = model or load_model()
        self.d, self.findings, self.judge = draft, findings, judge
        self.borrower, self.sources, self.hydrate = borrower, sources, hydrate
        self.agent_reading = agent_reading or {}
        self.obligation = obligation_state(draft, borrower, self.m)
        self.obs: list[str] = []
        self.requests: list[EvidenceRequest] = []

    def _date(self, f: AtomicFinding) -> str:
        return max(self.sources.get(s.source_id, ("", ""))[1] for s in f.spans)

    def _decisive(self, f: AtomicFinding) -> Decisive:
        quote = " … ".join(s.quote for s in f.spans)
        return Decisive(finding_id=f.finding_id, source_date=self._date(f),
                        quote=quote if len(quote) <= QUOTE_CHARS else quote[:QUOTE_CHARS] + "…")

    # --- Jev's readings ---------------------------------------------------------------------------

    async def _read(self, f: AtomicFinding, evidence: dict) -> FindingReading:
        direction = {"borrower": f"{self.borrower} must pay {self.d.counterparty}",
                     "counterparty": f"{self.d.counterparty} must pay {self.borrower}",
                     "not_stated": "The evidence does not say which party pays under this obligation"}
        obs = await self.judge.read(self.obligation, evidence, direction, (f.finding_id, self.d.instance_id))
        self.obs += [o.observation_id for o in obs]
        by = {o.question_id: o for o in obs}
        return FindingReading(
            finding_id=f.finding_id, source_date=self._date(f),
            payer=dict(by["obligation_direction"].probabilities or {}) if "obligation_direction" in by else {},
            amount_status=dict(by["amount_status"].probabilities or {}) if "amount_status" in by else {},
            includes_interest=by["amount_includes_interest"].noul_value if "amount_includes_interest" in by else None,
            events={ev: (by[f"event_{ev}"].noul_value if f"event_{ev}" in by else None) for ev in self.m["readings"]["events"]},
            observation_ids=tuple(o.observation_id for o in obs))

    def _asked(self, role: str) -> list[str]:
        """The factors asked for this obligation: a factor scoped to one payer (the payer's ability to fund, asked only
        when the counterparty pays; the borrower's own cash comes from its bank data) is skipped for the other."""
        payer = "borrower" if role == "debtor" else "counterparty"
        return [fid for fid, spec in self.m["factors"].items() if spec.get("payer", payer) == payer]

    async def _factors(self, f: AtomicFinding, r: FindingReading, evidence: dict, obligation: dict,
                       asked: list[str]) -> FindingReading:
        obs = await self.judge.relevance(obligation, evidence, (f.finding_id, self.d.instance_id), asked)
        self.obs += [o.observation_id for o in obs]
        by = {o.question_id: o for o in obs}
        bears = {fid: (by[f"bears_on_{fid}"].noul_value if f"bears_on_{fid}" in by else None) for fid in asked}
        levels, oids = {}, [o.observation_id for o in obs]
        for fid in asked:
            spec = self.m["factors"][fid]
            if spec["kind"] == "graded" and (bears.get(fid) or 0) >= ESTABLISHED:
                factor = {"factor_id": fid, "label": spec["label"], "question": spec["relevance"]}
                o = await self.judge.level(obligation, evidence, factor, spec["levels"], (f.finding_id, self.d.instance_id))
                self.obs.append(o.observation_id)
                oids.append(o.observation_id)
                levels[fid] = {spec["levels"][int(k)]: v for k, v in (o.probabilities or {}).items()
                               if str(k).isdigit() and int(k) < len(spec["levels"])}
        return r.model_copy(update={"bears_on": bears, "levels": levels,
                                    "observation_ids": r.observation_ids + tuple(oids)})

    # --- aggregation (code) -----------------------------------------------------------------------

    def _aggregate(self, readings: list[FindingReading]) -> list[FactorResult]:
        byid = {f.finding_id: f for f in self.findings}
        out = []
        for fid, spec in self.m["factors"].items():
            base = {"factor_id": fid, "label": spec["label"], "kind": spec["kind"], "aggregate": spec["aggregate"]}
            if spec["kind"] == "present":
                scored = [r for r in readings if r.bears_on.get(fid) is not None]
                if not scored:
                    out.append(FactorResult(**base))
                    continue
                top = max(scored, key=lambda r: r.bears_on[fid])
                p = top.bears_on[fid]
                out.append(FactorResult(**base, probability=p, level_label="established" if p >= ESTABLISHED else "not established",
                                        decisive=self._decisive(byid[top.finding_id]) if p >= ESTABLISHED else None,
                                        finding_ids=tuple(r.finding_id for r in scored if r.bears_on[fid] >= ESTABLISHED)))
                continue
            graded = [r for r in readings if r.levels.get(fid)]
            if not graded:
                out.append(FactorResult(**base))
                continue
            levels = spec["levels"]
            if spec["aggregate"] == "most_advanced":
                pick = [max(graded, key=lambda r: (expected_level(r.levels[fid], levels), r.source_date))]
            else:  # latest: the most recent passage(s); same-date disagreement is kept as a conflict
                newest = max(r.source_date for r in graded)
                pick = [r for r in graded if r.source_date == newest]
            dist = {lv: sum(r.levels[fid].get(lv, 0.0) for r in pick) / len(pick) for lv in levels}
            conflict = len({argmax(r.levels[fid]) for r in pick}) > 1
            out.append(FactorResult(**base, distribution=dist, conflict=conflict,
                                    level_label="conflicting readings" if conflict else argmax(dist),
                                    decisive=self._decisive(byid[pick[0].finding_id]),
                                    finding_ids=tuple(r.finding_id for r in graded)))
        return out

    def _request(self, factor_id: str, action: str) -> None:
        self.requests.append(EvidenceRequest(factor_id=factor_id, action=action,
                                             if_satisfied="The dispute enters the analysis.",
                                             if_not="The analysis shows this dispute as not modelled."))

    def _outside(self, update: dict) -> DisputeInstance:
        return self.d.model_copy(update={**update, "status": "outside_model", "evidence_requests": tuple(self.requests),
                                         "observation_ids": tuple(self.obs)})

    # --- run --------------------------------------------------------------------------------------

    async def run(self) -> DisputeInstance:
        update: dict = {"model_id": self.m["model_id"], "model_version": self.m["model_version"]}
        if self.judge is None:  # agent-only arm: the agent's own reading of the side and the stage
            role, stage = self.agent_reading.get("borrower_role"), self.agent_reading.get("stage")
            if role not in ("debtor", "creditor") or stage not in DATED_STAGES + ("amount_pending",):
                return self.d.model_copy(update={**update, "status": "not_judged"})
            return self.d.model_copy(update={**update, "borrower_role": role, "stage": stage, "status": "not_judged"})
        order = self.m["readings"]["amount_status_order"]
        evidence = {f.finding_id: self.hydrate(f) for f in self.findings}
        readings = list(await asyncio.gather(*(self._read(f, evidence[f.finding_id]) for f in self.findings)))
        payers = {argmax(r.payer) for r in readings} & {"borrower", "counterparty"}
        if len(payers) != 1:
            self._request("direction", f"Confirm from the order or judgment ({self.d.order_reference}) which party pays.")
            return self._outside({**update, "readings": tuple(readings)})
        role = "debtor" if payers == {"borrower"} else "creditor"
        obligation = {**self.obligation, "payer": self.borrower if role == "debtor" else self.d.counterparty}
        asked = self._asked(role)
        readings = list(await asyncio.gather(*(self._factors(f, r, evidence[f.finding_id], obligation, asked)
                                               for f, r in zip(self.findings, readings, strict=True))))
        factors = self._aggregate(readings)
        statuses = [argmax(r.amount_status) for r in readings]
        statuses = [s for s in statuses if s in order]
        status = max(statuses, key=order.index) if statuses else "unknown"
        interest = any((r.includes_interest or 0) >= ESTABLISHED for r in readings
                       if argmax(r.amount_status) not in (None, "not_stated"))
        byid = {f.finding_id: f for f in self.findings}
        established = {}
        for ev in self.m["readings"]["events"]:
            yes = [r for r in readings if (r.events.get(ev) or 0) >= ESTABLISHED]
            if yes:
                established[ev] = self._decisive(byid[max(yes, key=lambda r: r.source_date).finding_id])
        stage = next((rule["stage"] for rule in self.m["readings"]["stage_rules"] if rule["event"] in established), None)
        if status == "fixed" and stage == "amount_pending":
            stage = "judgment_entered"
        update.update({"borrower_role": role, "amount_status": status, "amount_includes_interest": interest,
                       "readings": tuple(readings), "factors": tuple(factors), "established": established,
                       "stage": stage})
        if stage == "paid" or status == "paid":
            return self.d.model_copy(update={**update, "status": "resolved", "observation_ids": tuple(self.obs)})
        if stage is None:
            self._request("stage", f"Obtain the docket for {self.d.order_reference} to establish where it stands.")
            return self._outside(update)
        if stage in DATED_STAGES and self.d.judgment_date is None:
            self._request("judgment_date", "Confirm the judgment's entry date from the docket.")
            return self._outside(update)
        return self.d.model_copy(update={**update, "status": "interpreted", "observation_ids": tuple(self.obs)})
