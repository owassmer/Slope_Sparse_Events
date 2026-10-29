"""The judge's answers to the dated walk's questions (design/002-exact-evaluator.md, The judge's input): each question
the reduce wrote (`pool.reduce`: its state's own parts and, by digest, its shared parts) asked alone through the
product's judge (`DisputeProfile.forecast`, the configured judgment provider), its answer as a distribution over its
answers in the order the walk offers them (a yes-or-no question's first answer is its 'yes'; a choice's, its
branches'), written by question identity for pass 2. Every answer is cached (`var/jev_cache`), so a stopped run
resumes; a question with no answer stops the run (an unknown is never a zero).

`python -m app.exact.judge <reduce dir> <answers.jsonl>`
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from app.disputes.forecast import answer_distribution
from app.exact import pool


def questions(reduce_dir: Path):
    """Every question line of the reduce output, with its state."""
    for lines in sorted(Path(reduce_dir).rglob("states-*.jsonl")):
        shared = json.loads(lines.with_suffix(".shared.json").read_text())
        for text in lines.open():
            line = json.loads(text)
            yield line, pool.state(line, shared)


async def answer_all(reduce_dir: Path, out: Path, log=print) -> dict:
    from app.agent.jev import JevAdapter
    from app.agent.jev_profiles import DisputeProfile
    from app.config import judgment_provider

    jev = JevAdapter(provider=judgment_provider(), run_id="exact-judge", use_cache=True,
                     spend_cap_usd=os.environ.get("SLOPE_JEV_CAP"))
    prof = DisputeProfile(jev, lambda kind, obj: None)
    items = list(questions(reduce_dir))
    t0, done = time.time(), 0
    batch = int(os.environ.get("SLOPE_JEV_BATCH", "200"))

    async def one(line, st):
        branches = tuple(line["branches"])
        if not line["verdict"] and len(branches) != line["answers"]:
            raise ValueError(f"{line['key']}: {len(branches)} answers, the walk offers {line['answers']}")
        o = await prof.forecast(line["question_id"], st, (line["instance_id"], *line["findings"]), branches)
        dist = answer_distribution(line["key"], branches, o)
        return {"id": line["id"], "key": line["key"], "verdict": line["verdict"], "branches": list(branches),
                "distribution": [dist[b] for b in branches], "confidence": o.confidence}

    with out.open("w") as fh:
        for lo in range(0, len(items), batch):
            for got in await asyncio.gather(*(one(line, st) for line, st in items[lo:lo + batch])):
                fh.write(json.dumps(got) + "\n")
            done = min(lo + batch, len(items))
            log(f"{time.time() - t0:7.0f}s {done}/{len(items)} answered; {jev.usage_summary()}")
    return jev.usage_summary()


def main(argv=None) -> None:
    args = argv or sys.argv[1:]
    print(asyncio.run(answer_all(Path(args[0]), Path(args[1]), log=lambda s: print(s, flush=True))))


if __name__ == "__main__":
    main()
