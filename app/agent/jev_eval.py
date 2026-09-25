"""Run the labelled semantic boundary cases against live Jev and report agreement per question.

This checks the registry questions and the code-owned 0.5 routing threshold on this project's own data
(TypeSafe's guidance: thresholds are use-case dependent). It sets no automatic confidence gate.
Host-only: the case file never enters an agent or reviewer run.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.jev import JevAdapter, registry_question
from app.config import RECORDED, ROOT, question_registry

CASES = ROOT / "evals" / "jev_semantic_cases.json"


async def _run(cases: list[dict], adapter: JevAdapter) -> list[dict[str, Any]]:
    async def one(case: dict) -> dict[str, Any]:
        call, [obs] = await adapter.judge(profile=case["profile"], question_ids=[case["question_id"]], state=case["state"],
                                          subject_ids=(case["case_id"],))
        got = obs.answer
        return {"case_id": case["case_id"], "borrower": case["borrower"], "question_id": case["question_id"],
                "question_version": registry_question(case["question_id"])["version"],
                "expected": case["expected"], "answer": got, "agree": got == case["expected"],
                "noul_value": obs.noul_value, "probabilities": obs.probabilities, "confidence": obs.confidence,
                "returned_model": call.returned_model, "cache_hit": call.cache_hit}
    return await asyncio.gather(*(one(c) for c in cases))


def run_eval(out: Path | None = None, *, use_cache: bool = False) -> dict[str, Any]:
    data = json.loads(CASES.read_text())
    adapter = JevAdapter(run_id=f"jev-eval-{datetime.now(UTC):%Y%m%dT%H%M%SZ}", use_cache=use_cache)
    results = asyncio.run(_run(data["cases"], adapter))
    by_q: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_q[r["question_id"]].append(r["agree"])
    report = {
        "run_at": datetime.now(UTC).isoformat(), "cases_file": str(CASES.relative_to(ROOT)),
        "registry_version": question_registry()["registry_version"], "cases_version": data["version"],
        "cases": len(results),
        "agreement": sum(r["agree"] for r in results), "noul_threshold": 0.5,
        "by_question": {q: {"agree": sum(v), "cases": len(v)} for q, v in sorted(by_q.items())},
        "jev_usage": adapter.usage_summary(), "results": results,
        "note": "Agreement with hand labels on a small boundary set; a sanity check, not an accuracy estimate.",
    }
    # Every run is kept (failures included): one file per run, never overwritten.
    out = out or RECORDED / "jev_semantic_eval" / f"{report['run_at'][:19].replace(':', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    report["path"] = str(out.relative_to(ROOT))
    return report
