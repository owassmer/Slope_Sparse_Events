"""The judge's input from the dated walk (app/exact/pool.py): a draw's questions, from its members written by pass 1's
map and read back by shard, each built into its state by the 14 May state builder: every question identity has its
state (the verdict form: one per question of the form), each shard holds only its own questions, and every member is
counted once."""
import io
import json

from app.exact import pass1, pool


def test_every_question_of_a_draw_has_its_state(tmp_path):
    fc, d, qt = pool.context()
    got = pass1.questions_on(fc, d, [365], shards=4, out_dir=tmp_path, log=lambda s: None)
    lines, ids, members_seen = [], set(), 0
    for shard in range(4):
        members = pool.load(tmp_path, shard)
        assert (pass1.shard_of(members["ids"][:, 1], 4) == shard).all()
        ids |= set(members["ids"][:, 1].tolist())
        members_seen += len(members["ids"])
        out = io.StringIO()
        pool.reduce(fc, d, qt, members, out, log=lambda s: None)
        lines += [json.loads(x) for x in out.getvalue().splitlines()]
    assert members_seen == got["draws"][365]["members"]
    assert {x["id"] for x in lines} == ids
    for x in lines:
        assert x["state"]["question"]["text"]
