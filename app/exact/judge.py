"""The judge's answers to the dated walk's questions (design/002-exact-evaluator.md, The judge's input): each question
the reduce wrote (`pool.reduce`: its state's own parts and, by digest, its shared parts) asked alone through the
product's judge (`DisputeProfile.forecast`, the configured judgment provider), its answer as a distribution over its
answers in the order the walk offers them (a yes-or-no question's first answer is its 'yes'; a choice's, its
branches'), written by question identity for pass 2. Every answer is cached (`var/jev_cache`), so a stopped run
resumes; a question with no answer stops the run (an unknown is never a zero).

`python -m app.exact.judge <reduce dir> <answers.jsonl> <case> [<first n questions>]`
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from app.agent.decisions import DecisionsError
from app.disputes.forecast import answer_distribution
from app.exact import pool


def questions(reduce_dir: Path):
    """Every question line of the reduce output, with its state."""
    for lines in sorted(Path(reduce_dir).rglob("states-*.jsonl")):
        shared = json.loads(lines.with_suffix(".shared.json").read_text())
        for text in lines.open():
            line = json.loads(text)
            yield line, pool.state(line, shared)


async def answer_all(reduce_dir: Path, out: Path, case: str, log=print, limit: int | None = None) -> dict:
    from app.agent.jev import JevAdapter
    from app.agent.jev_profiles import DisputeProfile
    from app.config import judgment_provider

    jev = JevAdapter(provider=judgment_provider(), run_id="exact-judge", use_cache=True,
                     spend_cap_usd=os.environ.get("SLOPE_JEV_CAP"))
    from app.agent import roles
    from app.disputes.rules import load_model

    parties = load_model(case).get("case_parties")
    prof = DisputeProfile(jev, lambda kind, obj: None, parties=parties)
    # the questions about one decision together (their shared evidence, read from the provider's cache, stays warm)
    items = sorted(questions(reduce_dir), key=lambda x: (x[0]["shared"], x[0]["key"]))[:limit]
    for _, st in items[:1]:  # the case's parties are the states' own (another case's would leave these names in)
        if roles.Parties(parties).role_of(st["case"]["company"]) != roles.Parties(parties).company:
            raise ValueError(f"{case} does not declare {st['case']['company']!r} as its company")
    t0, done = time.time(), 0
    batch = int(os.environ.get("SLOPE_JEV_BATCH", "200"))

    async def one(line, st):
        branches = tuple(line["branches"])
        if not line["verdict"] and len(branches) != line["answers"]:
            raise ValueError(f"{line['key']}: {len(branches)} answers, the walk offers {line['answers']}")
        try:
            o = await prof.forecast(line["question_id"], st, (line["instance_id"], *line["findings"]), branches)
        except DecisionsError as error:  # the provider failed after its retries: not answered; a rerun asks it again
            log(f"not answered (provider failure): {line['key'][:120]}: {str(error)[:120]}")
            return None
        try:
            dist = answer_distribution(line["key"], branches, o)
        except RuntimeError:  # refused or no probability: an unknown, recorded as such (pass 2 will not read it)
            return {"ids": line["ids"], "key": line["key"], "verdict": line["verdict"], "branches": list(branches),
                    "refused": True}
        return {"ids": line["ids"], "key": line["key"], "verdict": line["verdict"], "branches": list(branches),
                "distribution": [dist[b] for b in branches], "confidence": o.confidence}

    refused = failed = 0
    with out.open("w") as fh:
        for lo in range(0, len(items), batch):
            for got in await asyncio.gather(*(one(line, st) for line, st in items[lo:lo + batch])):
                if got is None:
                    failed += 1
                    continue
                fh.write(json.dumps(got) + "\n")
                refused += bool(got.get("refused"))
            done = min(lo + batch, len(items))
            log(f"{time.time() - t0:7.0f}s {done}/{len(items)} asked, {refused} refused, {failed} not answered; "
                f"{jev.usage_summary()}")
    return {**jev.usage_summary(), "refused": refused, "not_answered": failed}


def main(argv=None) -> None:
    """`python -m app.exact.judge <reduce dir> <answers.jsonl> <case> [<first n questions>]`"""
    args = argv or sys.argv[1:]
    limit = int(args[3]) if len(args) > 3 else None
    got = asyncio.run(answer_all(Path(args[0]), Path(args[1]), args[2], log=lambda s: print(s, flush=True),
                                 limit=limit))
    print(got)
    sys.exit(1 if got["not_answered"] else 0)  # a rerun asks only those (the rest come from the cache)


if __name__ == "__main__":
    main()
