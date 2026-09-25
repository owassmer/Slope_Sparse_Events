"""Run-scoped investigation store: an append-only, hash-chained event log plus the graph it implies.

Every change to the investigation is an event in `var/runs/<run_id>/events.jsonl`. The in-memory graph
is a fold over those events, so a run can be replayed, verified and rendered without trusting mutable
state. `lock()` freezes the run (no further events) and writes `packet.json` with the chain head.
Scoping is by run_id: repeated evaluations never share mutable state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import VAR
from app.domain.investigation import (
    AtomicFinding,
    DecisionDependency,
    DisputeNode,
    EconomicEffectProposal,
    EventKind,
    EvidenceCandidate,
    InventoryItem,
    InvestigationEvent,
    JevCallRecord,
    ReconciliationTask,
    SemanticObservation,
)

GENESIS = "0" * 64
REMOVED_FIELDS = {("inventory", "exclusions")}  # recorded by run 13 under a design later removed
COLLECTIONS: dict[str, type[BaseModel]] = {
    "dependencies": DecisionDependency, "candidates": EvidenceCandidate, "jev_calls": JevCallRecord,
    "observations": SemanticObservation, "findings": AtomicFinding, "reconciliations": ReconciliationTask,
    "effects": EconomicEffectProposal, "inventory": InventoryItem, "dispute_nodes": DisputeNode,
}
# Which collection each event kind writes its object into (None = log-only event).
EVENT_COLLECTION: dict[str, str | None] = {
    "dependency_recorded": "dependencies", "candidate_screened": "candidates", "jev_call": "jev_calls",
    "observation_recorded": "observations", "observation_disposition": "observations",
    "finding_proposed": "findings", "finding_resolved": "findings", "reconciliation_opened": "reconciliations",
    "reconciliation_resolved": "reconciliations", "effect_proposed": "effects", "effect_validated": "effects",
    "inventory_loaded": "inventory", "inventory_accounted": "inventory", "effect_disputed": "effects",
    "dispute_node_recorded": "dispute_nodes",
}
ID_FIELD = {"dependencies": "dependency_id", "candidates": "candidate_id", "jev_calls": "call_id",
            "observations": "observation_id", "findings": "finding_id", "reconciliations": "task_id",
            "effects": "effect_id", "inventory": "item_id", "dispute_nodes": "node_id"}


class LockedRunError(RuntimeError):
    pass


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class RunStore:
    def __init__(self, run_id: str, *, root: Path | None = None, meta: dict[str, Any] | None = None) -> None:
        self.run_id = run_id
        self.dir = (root or VAR / "runs") / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "events.jsonl"
        self.events: list[InvestigationEvent] = []
        self.graph: dict[str, dict[str, BaseModel]] = {name: {} for name in COLLECTIONS}
        self.counters: dict[str, int] = {}
        self.locked = (self.dir / "packet.json").exists()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                self._apply(InvestigationEvent.model_validate_json(line))
            self.verify()
            if self.locked:  # a locked run's log must still end exactly where the packet says
                packet = json.loads((self.dir / "packet.json").read_text())
                if (packet["chain_head"] != self.head or packet["event_count"] != len(self.events)
                        or not self._packet_matches(packet)):
                    raise ValueError(f"Locked run {run_id}: event log does not match its packet")
        elif meta is not None:
            self.append("run_started", payload={"run_id": run_id, **meta})

    # --- ids and events ------------------------------------------------------------------------

    def new_id(self, prefix: str) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}_{n:03d}"

    @property
    def head(self) -> str:
        return self.events[-1].hash if self.events else GENESIS

    def append(self, kind: EventKind, obj: BaseModel | None = None, *, object_ids: tuple[str, ...] = (),
               payload: dict[str, Any] | None = None) -> InvestigationEvent:
        if self.locked or (self.dir / "packet.json").exists():  # another instance may have locked it
            self.locked = True
            raise LockedRunError(f"Run {self.run_id} is locked")
        body = dict(payload or {})
        if obj is not None:
            # model_copy() does not validate; re-validate so an invalid object can never reach the log.
            obj = type(obj).model_validate(obj.model_dump(mode="json"))
            body["object"] = obj.model_dump(mode="json")
        seq = len(self.events) + 1
        at = datetime.now(UTC).isoformat()
        digest = hashlib.sha256((self.head + _canonical({"seq": seq, "at": at, "kind": kind, "object_ids": object_ids,
                                                         "payload": body})).encode()).hexdigest()
        event = InvestigationEvent(seq=seq, at=at, kind=kind, object_ids=object_ids, payload=body,
                                   prev_hash=self.head, hash=digest)
        with self.path.open("a") as fh:
            fh.write(event.model_dump_json() + "\n")
        self._apply(event)
        return event

    def _apply(self, event: InvestigationEvent) -> None:
        self.events.append(event)
        collection = EVENT_COLLECTION.get(event.kind)
        ids = list(event.object_ids)
        if collection and "object" in event.payload:
            model = COLLECTIONS[collection].model_validate(event.payload["object"])
            key = getattr(model, ID_FIELD[collection])
            self.graph[collection][key] = model
            ids.append(key)
        for key in ids:  # replay restores every ID counter (objects and log-only IDs such as searches)
            prefix, _, n = key.rpartition("_")
            if prefix and n.isdigit():
                self.counters[prefix] = max(self.counters.get(prefix, 0), int(n))

    def _packet_matches(self, packet: dict[str, Any]) -> bool:
        """Every collection and field recorded in the packet must equal the replayed log.

        Schema evolution only adds collections or defaulted fields; those are not in older packets, so they are
        not compared. Any altered or missing recorded value still fails.
        """
        current = json.loads(json.dumps(self.export(), default=str))
        for name, model in COLLECTIONS.items():
            ids = ID_FIELD[name]
            replayed = {o[ids]: o for o in current.get(name, [])}
            if name not in packet:
                if replayed:  # a collection with content cannot be missing from the packet
                    return False
                continue
            recorded = {o[ids]: o for o in packet[name]}
            if recorded.keys() != replayed.keys():
                return False
            defaults = {f: json.loads(json.dumps(info.get_default(call_default_factory=True), default=str))
                        for f, info in model.model_fields.items() if not info.is_required()}
            for key, obj in recorded.items():
                # Only a field a later schema removed is skipped (it stays protected by the hash-chained event log);
                # any other field the packet holds must equal the replayed value, so a forged field is still caught.
                if any(replayed[key].get(field) != value for field, value in obj.items()
                       if (name, field) not in REMOVED_FIELDS):
                    return False
                # A field absent from the packet is acceptable only when the replayed value is its default
                # (a field added by a later schema version), never when a recorded value was deleted.
                missing = set(replayed[key]) - set(obj)
                if any(f not in defaults or replayed[key][f] != defaults[f] for f in missing):
                    return False
        return True

    def verify(self) -> None:
        prev = GENESIS
        for e in self.events:
            expected = hashlib.sha256((prev + _canonical({"seq": e.seq, "at": e.at, "kind": e.kind,
                                                          "object_ids": e.object_ids,
                                                          "payload": e.payload})).encode()).hexdigest()
            if e.prev_hash != prev or e.hash != expected:
                raise ValueError(f"Event chain broken at seq {e.seq}")
            prev = e.hash

    # --- typed accessors ----------------------------------------------------------------------

    def get(self, collection: str, key: str) -> BaseModel:
        try:
            return self.graph[collection][key]
        except KeyError:
            raise KeyError(f"{key} is not in this run's {collection}") from None

    def put(self, kind: EventKind, obj: BaseModel, **kw: Any) -> BaseModel:
        collection = EVENT_COLLECTION[kind]
        self.append(kind, obj, object_ids=(getattr(obj, ID_FIELD[collection]),), **kw)
        return obj

    def export(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "head": self.head, "event_count": len(self.events),
                **{name: [m.model_dump(mode="json") for m in items.values()] for name, items in self.graph.items()}}

    def lock(self, summary: dict[str, Any] | None = None) -> Path:
        self.append("packet_submitted", payload=summary or {})
        packet = {"locked_at": datetime.now(UTC).isoformat(), "chain_head": self.head, **self.export()}
        # export() already carries event_count; the loader checks head and count against the log.
        out = self.dir / "packet.json"
        out.write_text(json.dumps(packet, indent=2, default=str) + "\n")
        self.locked = True
        return out
