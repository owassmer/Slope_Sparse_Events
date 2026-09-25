"""Compile one live dispute onto the dispute model: from Jev's atomic readings of each cited passage to every cash
path the evidence still permits.

Jev reads each accepted finding's quotes against one obligation (who pays whom, the amount's status, which procedural
events it establishes, which factors it bears on and at what level); it never forecasts an outcome. Code owns the
rest: the stage (the most advanced established event), each factor's aggregation rule, the few paths an established
fact closes, every date and amount (rules.py), and the ordinal "the record points here" marks. No path is weighted or
pruned: Slope's policy is tested on all of them. Without Jev (the agent-only arm) the agent's own reading of the side
and the stage is used, and no factors are established.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Protocol

from app.disputes.rules import Window, amount_range, cash_for, load_model, next_window
from app.domain.investigation import (
    AtomicFinding,
    BranchCash,
    Decisive,
    DisputeInstance,
    DisputePath,
    EvidenceRequest,
    FactorResult,
    FindingReading,
    SemanticObservation,
)

DATED_STAGES = ("judgment_entered", "appeal_pending", "enforcement")
QUOTE_CHARS = 300


class DisputeJudge(Protocol):
    async def read(self, obligation: dict, cited: dict, direction: dict[str, str],
                   subject_ids: tuple[str, ...]) -> list[SemanticObservation]: ...

    async def relevance(self, obligation: dict, cited: dict, subject_ids: tuple[str, ...]) -> list[SemanticObservation]: ...

    async def level(self, obligation: dict, cited: dict, factor: dict, rubric: list[str],
                    subject_ids: tuple[str, ...]) -> SemanticObservation: ...


def _by_question(observations: list[SemanticObservation]) -> dict[str, SemanticObservation]:
    return {o.question_id: o for o in observations}


class Compiler:
    def __init__(self, draft: DisputeInstance, findings: list[AtomicFinding], *, judge: DisputeJudge | None,
                 borrower: str, sources: dict[str, tuple[str, str]], review: date, horizon: date,
                 agent_reading: dict | None = None, model: dict | None = None) -> None:
        self.m = model or load_model()
        self.d, self.findings, self.judge = draft, findings, judge
        self.borrower, self.sources, self.review, self.horizon = borrower, sources, review, horizon
        self.agent_reading = agent_reading or {}
        self.obligation = {"what": draft.obligation or draft.title, "parties": [borrower, draft.counterparty]}
        self.obs: list[str] = []
        self.requests: list[EvidenceRequest] = []

    # --- Jev's readings ---------------------------------------------------------------------------

    def _date(self, f: AtomicFinding) -> str:
        return max(self.sources.get(s.source_id, ("", ""))[1] for s in f.spans)

    def _cited(self, f: AtomicFinding) -> dict:
        titles = sorted({self.sources.get(s.source_id, (s.source_id, ""))[0] for s in f.spans})
        return {"source": "; ".join(titles), "source_date": self._date(f), "quotes": [s.quote for s in f.spans]}

    def _decisive(self, f: AtomicFinding) -> Decisive:
        quote = " … ".join(s.quote for s in f.spans)
        return Decisive(finding_id=f.finding_id, source_date=self._date(f),
                        quote=quote if len(quote) <= QUOTE_CHARS else quote[:QUOTE_CHARS] + "…")

    async def _read(self, f: AtomicFinding) -> FindingReading:
        direction = {"borrower": f"{self.borrower} must pay {self.d.counterparty}",
                     "counterparty": f"{self.d.counterparty} must pay {self.borrower}",
                     "not_stated": "The text does not say which party pays under this obligation"}
        obs = await self.judge.read(self.obligation, self._cited(f), direction, (f.finding_id, self.d.instance_id))
        self.obs += [o.observation_id for o in obs]
        by = _by_question(obs)
        events = {ev: by[f"event_{ev}"].answer if f"event_{ev}" in by else None for ev in self.m["readings"]["events"]}
        return FindingReading(finding_id=f.finding_id, source_date=self._date(f),
                              payer=by["obligation_direction"].answer if "obligation_direction" in by else None,
                              amount_status=by["amount_status"].answer if "amount_status" in by else None,
                              includes_interest=by["amount_includes_interest"].answer if "amount_includes_interest" in by else None,
                              events=events, observation_ids=tuple(o.observation_id for o in obs))

    async def _factors_for(self, f: AtomicFinding, r: FindingReading, obligation: dict) -> FindingReading:
        obs = await self.judge.relevance(obligation, self._cited(f), (f.finding_id, self.d.instance_id))
        self.obs += [o.observation_id for o in obs]
        by = _by_question(obs)
        bears = {fid: by[f"bears_on_{fid}"].answer if f"bears_on_{fid}" in by else None for fid in self.m["factors"]}
        levels, oids = {}, [o.observation_id for o in obs]
        for fid, spec in self.m["factors"].items():
            if spec["kind"] == "graded" and bears.get(fid):
                factor = {"factor_id": fid, "label": spec["label"], "question": spec["relevance"]}
                o = await self.judge.level(obligation, self._cited(f), factor, spec["levels"],
                                           (f.finding_id, self.d.instance_id))
                self.obs.append(o.observation_id)
                oids.append(o.observation_id)
                levels[fid] = None if o.answer is None else int(o.answer)
        return r.model_copy(update={"bears_on": bears, "levels": levels,
                                    "observation_ids": r.observation_ids + tuple(oids)})

    # --- aggregation (code) -----------------------------------------------------------------------

    def _aggregate(self, readings: list[FindingReading]) -> list[FactorResult]:
        byid = {f.finding_id: f for f in self.findings}
        out = []
        for fid, spec in self.m["factors"].items():
            routed = [r for r in readings if r.bears_on.get(fid)]
            base = {"factor_id": fid, "label": spec["label"], "kind": spec["kind"], "aggregate": spec["aggregate"],
                    "finding_ids": tuple(r.finding_id for r in routed)}
            if spec["kind"] == "present":
                if routed:
                    latest = max(routed, key=lambda r: r.source_date)
                    out.append(FactorResult(**base, level=1, level_label="established",
                                            decisive=self._decisive(byid[latest.finding_id])))
                else:
                    out.append(FactorResult(**base, level=0, level_label="not established"))
                continue
            graded = [r for r in routed if r.levels.get(fid) is not None]
            if not graded:
                out.append(FactorResult(**base))
                continue
            if spec["aggregate"] == "most_advanced":
                pick = max(graded, key=lambda r: (r.levels[fid], r.source_date))
                conflict = False
            else:  # latest: the most recent passage; same-date disagreement is kept as a conflict
                newest = max(r.source_date for r in graded)
                same = [r for r in graded if r.source_date == newest]
                pick, conflict = same[0], len({r.levels[fid] for r in same}) > 1
            level = None if conflict else pick.levels[fid]
            out.append(FactorResult(**base, level=level, conflict=conflict,
                                    level_label="conflicting readings" if conflict else spec["levels"][level],
                                    decisive=self._decisive(byid[pick.finding_id])))
        return out

    # --- paths (code) -----------------------------------------------------------------------------

    @staticmethod
    def _holds(cond: dict, factors: dict[str, FactorResult]) -> bool:
        f = factors.get(cond["factor"])
        if f is None or f.level is None:
            return False
        if "present" in cond:
            return bool(f.level) == cond["present"]
        if "at_least" in cond:
            return f.level >= cond["at_least"]
        return f.level <= cond["at_most"]

    def _note(self, cond: dict, factors: dict[str, FactorResult]) -> str:
        f = factors[cond["factor"]]
        return f"{f.label}: {f.level_label} ({f.decisive.finding_id}: “{f.decisive.quote}”)" if f.decisive else f.label

    def _expand(self, stage: str, at: Window, path: list[dict], cash: tuple[BranchCash, ...],
                factors: dict[str, FactorResult], closed: dict[str, str], out: list[tuple]) -> None:
        for t in self.m["transitions"][stage]:
            key = f"{stage}.{t['transition_id']}"
            if any(self._holds(c, factors) for c in t.get("closed_if", [])):
                closed[key] = "; ".join(self._note(c, factors) for c in t["closed_if"] if self._holds(c, factors))
                continue
            step = cash_for(self.m, t["rule"], self.d.borrower_role, self.d.amount, at, self.review, self.horizon,
                            self.d.finding_ids, self.d.amount_includes_interest)
            points = tuple(self._note(c, factors) for c in t.get("points_if", []) if self._holds(c, factors))
            node = {**t, "points": points}
            if self.m["stages"][t["to"]]["terminal"]:
                out.append(([*path, node], t["to"], cash + tuple(step)))
            else:
                self._expand(t["to"], next_window(self.m, t["rule"], t["to"], at, self.review), [*path, node],
                             cash + tuple(step), factors, closed, out)

    def _paths(self, raw: list[tuple]) -> tuple[DisputePath, ...]:
        """Every permitted path; paths with identical cash are merged (their labels kept), since they test the same."""
        merged: dict[tuple, list] = {}
        for path, terminal, cash in raw:
            key = tuple((c.kind, *amount_range(c.amount), c.window_start, c.window_end) for c in cash)
            merged.setdefault(key, []).append((path, terminal, cash))
        out = []
        for i, group in enumerate(merged.values(), 1):
            path, terminal, cash = group[0]
            out.append(DisputePath(
                path_id=f"{self.d.instance_id}_p{i}", transitions=tuple(t["transition_id"] for t in path),
                labels=tuple(t["label"] for t in path), terminal=terminal, cash=cash,
                points_here=tuple(dict.fromkeys(p for t in path for p in t["points"])),
                also=tuple(" → ".join(t["label"] for t in g[0]) for g in group[1:])))
        return tuple(out)

    # --- run --------------------------------------------------------------------------------------

    def _request(self, factor_id: str, action: str, if_satisfied: str, if_not: str) -> None:
        self.requests.append(EvidenceRequest(factor_id=factor_id, action=action, if_satisfied=if_satisfied,
                                             if_not=if_not))

    def _outside(self, update: dict) -> DisputeInstance:
        return self.d.model_copy(update={**update, "status": "outside_model", "evidence_requests": tuple(self.requests),
                                         "observation_ids": tuple(self.obs)})

    async def run(self) -> DisputeInstance:
        update: dict = {"model_id": self.m["model_id"], "model_version": self.m["model_version"]}
        order = self.m["readings"]["amount_status_order"]
        if self.judge is None:  # agent-only arm: the agent's own reading, no factors
            role, stage = self.agent_reading.get("borrower_role"), self.agent_reading.get("stage")
            if role not in ("debtor", "creditor") or stage not in DATED_STAGES + ("amount_pending",):
                return self.d.model_copy(update={**update, "status": "not_judged"})
            factors, readings, status, established = [], [], "unknown", {}
            self.d = self.d.model_copy(update={"borrower_role": role})
        else:
            readings = list(await asyncio.gather(*(self._read(f) for f in self.findings)))
            payers = {r.payer for r in readings if r.payer in ("borrower", "counterparty")}
            if len(payers) != 1:
                self._request("direction", f"Confirm from the judgment or order which party pays under {self.obligation['what']}.",
                              "The dispute's paths enter the event-adjusted view.",
                              "The event-adjusted recommendation stays incomplete for this dispute.")
                return self._outside({**update, "readings": tuple(readings)})
            role = "debtor" if payers == {"borrower"} else "creditor"
            payer = self.borrower if role == "debtor" else self.d.counterparty
            obligation = {**self.obligation, "payer": payer}
            readings = list(await asyncio.gather(*(self._factors_for(f, r, obligation)
                                                   for f, r in zip(self.findings, readings, strict=True))))
            factors = self._aggregate(readings)
            statuses = [r.amount_status for r in readings if r.amount_status in order]
            status = max(statuses, key=order.index) if statuses else "unknown"
            byid = {f.finding_id: f for f in self.findings}
            established = {}
            for ev in self.m["readings"]["events"]:
                yes = [r for r in readings if r.events.get(ev)]
                if yes:
                    established[ev] = self._decisive(byid[max(yes, key=lambda r: r.source_date).finding_id])
            stage = next((rule["stage"] for rule in self.m["readings"]["stage_rules"] if rule["event"] in established),
                         None)
            if status == "fixed" and stage == "amount_pending":
                stage = "judgment_entered"
            self.d = self.d.model_copy(update={
                "borrower_role": role, "amount_status": status,
                "amount_includes_interest": any(r.includes_interest for r in readings)})
        update.update({"borrower_role": self.d.borrower_role, "amount_status": status, "readings": tuple(readings),
                       "factors": tuple(factors), "established": established, "stage": stage,
                       "amount_includes_interest": self.d.amount_includes_interest})
        if stage == "paid" or status == "paid":
            return self.d.model_copy(update={**update, "status": "resolved", "observation_ids": tuple(self.obs)})
        if stage is None:
            self._request("stage", f"Obtain the docket for {self.obligation['what']} to establish where it stands.",
                          "The dispute's paths enter the event-adjusted view.",
                          "The event-adjusted recommendation stays incomplete for this dispute.")
            return self._outside(update)
        if stage in DATED_STAGES and self.d.judgment_date is None:
            self._request("judgment_date", "Confirm the judgment's entry date from the docket.",
                          "The dispute's paths are dated from it.",
                          "The event-adjusted recommendation stays incomplete for this dispute.")
            return self._outside(update)
        if status in ("sought", "estimated"):
            label = self.m["readings"]["amount_status_labels"][status]
            self._request("amount_status", f"Obtain the court's ruling on the amount of {self.obligation['what']} "
                                           f"(now {label}).",
                          "The fixed amount replaces the figure on every path.",
                          "Every path keeps the figure as its ceiling.")
        at = Window(self.d.judgment_date, self.d.judgment_date) if stage in DATED_STAGES else Window(self.review, self.review)
        raw: list[tuple] = []
        closed: dict[str, str] = {}
        self._expand(stage, at, [], (), {f.factor_id: f for f in factors}, closed, raw)
        return self.d.model_copy(update={
            **update, "status": "compiled" if self.judge else "not_judged", "paths": self._paths(raw), "closed": closed,
            "evidence_requests": tuple(self.requests), "observation_ids": tuple(self.obs)})
