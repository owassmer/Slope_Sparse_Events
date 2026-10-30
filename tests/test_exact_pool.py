"""The judge's input from the dated walk (app/exact/pool.py): a draw's questions, from its members written by pass 1's
map and read back by shard, each built into its state by the 14 May state builder: every question identity has its
state (the verdict form: one per question of the form), each shard holds only its own questions, every situation is
built once, and every walk identity pass 1 found is answered for (a record reached under two identities, both)."""
import io
import json

import numpy as np

from app.exact import pass1, pool


def test_every_question_of_a_draw_has_its_state(tmp_path):
    fc, d, qt, tk = pool.context()
    pass1.questions_on(fc, d, [365], shards=3, out_dir=tmp_path, log=lambda s: None)
    lines, ids, shared = [], set(), {}
    for shard in range(3):
        members = pool.load(tmp_path, shard, 3, qt)
        for i in np.unique(members["question"]):  # each question in its shard, under its text's identity
            j = int(np.flatnonzero(members["question"] == i)[0])
            text = pool.render(members["rows"][j], members["cls"][j], qt)
            assert pool.shard_of_question(text, 3) == shard and pool.question_id(text) == i
        out = io.StringIO()
        pool.reduce(fc, d, qt, members, out, log=lambda s: None, tk=tk, shared=shared)
        lines += [json.loads(x) for x in out.getvalue().splitlines()]
    for f in tmp_path.glob("questions-*.npz"):  # the walk's own identities, as pass 1 wrote them
        ids |= set(np.load(f)["ids"][:, 1].tolist())
    assert set().union(*(x["ids"] for x in lines)) == ids  # every walk identity answered for
    keys = [x["key"] for x in lines]
    assert len(keys) == len(set(keys))  # each question built once
    for x in lines:
        st = pool.state(x, shared)
        assert st["question"]["text"] and list(st) == x["order"]


def test_what_each_question_is_asked_on_is_the_walks():
    """Every question the Python walk creates on a sample of histories is asked on what the table says
    (`pool.assumptions`)."""
    from app.disputes.answer_first import AnswerFirstWalk, Guide
    from app.exact import oracle

    seen, kinds = 0, set()
    for seed, style in ((3, "uniform"), (11, "long"), (19, "long")):
        fc, d = oracle.context((0, 145, 365))
        guide = Guide(fc, oracle._long if style == "long" else
                      (lambda key, branches: dict.fromkeys(branches, 1 / len(branches))))
        AnswerFirstWalk(fc, d).configured(guide=guide, tau=2, groups=6, min_groups=6, group_size=1, enumerate_days=0,
                                          seed=seed).run()
        for n in fc.nodes.values():
            if "#" in n.key or n.node.startswith("verdict"):
                continue
            ctx = [c for c in n.context.split("|") if c]
            assert n.assumptions == pool.assumptions(fc, n.node, ctx), (n.key, n.assumptions)
            seen += 1
            kinds.add(n.node)
    assert seen >= 15 and len(kinds) >= 8, (seen, sorted(kinds))
