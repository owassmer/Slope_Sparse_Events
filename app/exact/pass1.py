"""Pass 1's Python side: the case's tables for the walk (`tree.TK`) and a draw's walk run (`tree.explore`)."""
from __future__ import annotations

import numpy as np

from app.analysis.events import MARKS
from app.exact import fields_record as fr
from app.exact import fields_walk as w
from app.exact import replay, tree, walk


def case_tables(fc, d, ch0) -> tree.TK:
    """The case's tables: the verdict classes (`Forecaster.verdict_classes`, over the forecaster's draws) as encoded
    steps with their reduced bands, the question nodes' situation conditions, the offering bound, the notice lag."""
    from app.disputes.forecast import _Walk

    classes = list(fc.verdict_classes(d))
    verdicts = np.array([replay.encode(ch0, ("verdict", "I0", b)) for b in classes], dtype=np.int64)
    reduced = np.full((len(classes), 3), -1, dtype=np.int64)
    for i, b in enumerate(classes):
        if b.startswith("award:"):
            band = fc.reduced_band(d, int(b.split(":")[1]))
            if band is not None:
                lo, hi, booked = band
                reduced[i] = booked, lo, hi
    wlk = _Walk(fc, d)
    conds = np.zeros(len(w.QNODES), dtype=np.int64)
    appeals = np.zeros(len(w.QNODES), dtype=np.bool_)
    stays = np.zeros(len(w.QNODES), dtype=np.bool_)
    for q, name in enumerate(w.QNODES):
        if name in fc.spec:
            for c in set(wlk.situation_conditions(name)):
                if c in MARKS:
                    conds[q] |= 1 << MARKS.index(c)
        if name != "verdict":
            node = fc.new_node(d, name)
            appeals[q] = fc.uses_appeal_status(node) or name == "enforce_after_final"
            stays[q] = fc.uses_stay_status(node)
    ec = replay.export_equity.config(ch0)
    nc = replay.export_notes.config(ch0)
    return tree.TK(listing=ch0.fin is not None and ch0.fin.listing_deadline is not None, verdicts=verdicts,
                   reduced=reduced, conds=conds, bound=int(ec.bound), notice=int(nc.notice), appeals=appeals,
                   stays=stays)


def explore(ch0, r: int, tk: tree.TK, budget: int = 10**9, qcap: int = 10**6, forced=()):
    """Pass 1 on draw row r: (questions [n, QCOLS], nodes, leaves, deepest, the watched keys read on the draw, the
    classes [n, NC], per question its record's digest and identity [n, 2], the final cumulative event cash's range
    (lo, hi) per day). forced: the watched keys read on some draw, asked wherever they arise (`depth`)."""
    k, ln = replay.constants(ch0, r)
    st = replay.state(ch0, r, ln)
    ws = replay.workspace(ch0, r, st)
    wk = walk.WK(listing=tk.listing)
    sk = tree.stack_for(st)
    out = np.zeros((qcap, w.QCOLS), dtype=np.int64)
    cls = np.zeros((qcap, fr.NC), dtype=np.int64)
    dig = np.zeros((qcap, 2), dtype=np.int64)
    reads = np.zeros(4096, dtype=np.int64)
    forced = np.array(sorted(forced), dtype=np.int64)
    ev_lo, ev_hi = np.zeros(ch0.N, np.int64), np.zeros(ch0.N, np.int64)
    nq, nodes, leaves, deepest, nr = tree.explore(st, k, wk, tk, ws, sk, out, cls, dig, budget, forced, reads,
                                                  ev_lo, ev_hi)
    if nr > reads.size:
        raise ValueError("more watched reads than the buffer holds")
    m = min(nq, qcap)
    return out[:m], nodes, leaves, deepest, set(reads[:nr].tolist()), cls[:m], dig[:m], (ev_lo, ev_hi)


def depth(run_draw) -> tuple[set, int]:
    """QUESTIONS §1 Depth over the draws: `run_draw(forced) -> keys read` for each draw (all draws walked by the
    caller); the watched questions read on some draw are asked on every draw where they arise, which can expose
    further reads, so the draws are walked again until no new key is read. Returns (the keys asked, rounds)."""
    forced: set = set()
    rounds = 0
    while True:
        rounds += 1
        read = run_draw(frozenset(forced))
        if read <= forced:
            return forced, rounds
        forced |= read


G = {"pay": tree.G_PAY, "initiate_offering": tree.G_OFFER, "file": tree.G_FILE, "none": tree.G_NONE,
     "neither": tree.G_NONE}


def script_of(fc, d, ch0, steps):
    """A Python walk history's answers as the dated walk's elementary decisions: (script by decision id, the offering
    outcomes by occasion). A composite label stands for the conjunction the walk takes first where it names several
    (enforcement 'none': no request; 'set_aside': set aside outright)."""
    from app.analysis.events import plain
    from app.exact import fields_agenda as a

    script = np.full(w.N_DEC, tree.NO_STEP, dtype=np.int64)
    n1 = np.full(len(a.OCCASIONS), tree.NO_STEP, dtype=np.int64)
    classes = list(fc.verdict_classes(d))
    yn = {"yes": a.YES, "no": a.NO}
    for node, ctx, branch in steps:
        b = plain(branch)
        unasked = branch.startswith("@-1") or (node == "settle" and branch.startswith("@0="))  # settle's group 0:
        #                                                                                        no settlement
        if node == "settle":
            script[w.DEC_SETTLE0 + a.SETTLE_CTX.index(ctx)] = tree.UNASKED_STEP if unasked else yn[b]
        elif node == "verdict":
            script[w.DEC_VERDICT] = classes.index(b)
        elif node == "judgment_response":
            script[w.DEC_RESP0 + a.RESPONSE_CTX.index(ctx)] = tree.UNASKED_STEP if unasked else G[b]
        elif node == "offering":
            n1[a.OCCASIONS.index(ctx)] = yn[b]
        elif node == "post_trial_motions":
            script[w.DEC_MOTIONS] = yn[b]
        elif node == "execute_pre_ruling":
            script[w.DEC_EXECUTE] = yn[b]
        elif node == "appeal":
            script[w.DEC_APPEAL] = yn[b]
        elif node == "stay":
            p = a.PHASE_CTX.index(ctx)
            script[w.DEC_STAY0 + p] = a.NO if b == "no" else a.YES
            if b != "no":
                script[w.DEC_APPROVAL0 + p] = a.YES if b == "yes" else a.DENIED
        elif node == "registration_early":
            script[w.DEC_REGISTRATION] = yn[b]
        elif node == "judgment_default":
            o = a.JDEFAULT_CTX.index(ctx)
            script[w.DEC_JDEFAULT0 + o] = a.NO if b == "no" else a.YES
            if b != "no":
                script[w.DEC_A5_0 + o] = a.YES if b == "yes" else a.NO
                if b != "yes":
                    script[w.DEC_H3_0 + o] = a.YES if b == "holders_file" else a.NO
        elif node == "post_trial_ruling":
            script[w.DEC_RULING] = (tree.RU_REDUCED_ACCEPTED if b.startswith("reduced") else
                                    tree.RU_SET_ASIDE if b == "set_aside" else tree.RU_UNCHANGED)
        elif node == "enforce":
            script[w.DEC_ENFORCE] = a.E_LEVY if b == "levy" else a.E_NONE
            if b == "levy":
                script[w.DEC_REG_POST] = a.YES
        elif node == "listing":
            script[w.DEC_LISTING] = a.YES if b == "compliant" else a.NO
            if b != "compliant":
                script[w.DEC_HEARING] = a.YES if b == "hearing" else a.NO
        elif node == "delisting_notes":
            o = w.O_DELISTING
            script[w.DEC_DELISTING] = tree.H2_NEITHER if b == "none" else tree.H2_ACCELERATE
            if b != "none":
                script[w.DEC_A5_0 + o] = a.YES if b == "petition_delist" else a.NO
                if b != "petition_delist":
                    script[w.DEC_H3_0 + o] = a.YES if b == "petition_delist_holders" else a.NO
        elif node == "cash_floor":
            script[w.DEC_FLOOR0 + int(ctx)] = tree.UNASKED_STEP if unasked else G[b]
        elif node == "cash_out":
            script[w.DEC_CASH_OUT] = tree.UNASKED_STEP if unasked else G[b]
        elif node == "nonpayment":
            script[w.DEC_NONPAYMENT] = {"petition": a.NP_PETITION, "holders_file": a.NP_HOLDERS, "due": a.NP_DUE}[b]
        else:
            raise NotImplementedError(node)
    return script, n1


def follow(fc, d, ch0, r: int, tk, steps, questions: bool = False):
    """The history `steps` followed on draw row r: (findings [n, 4], the state at the end, the script, the offering
    outcomes; questions: also each question asked, (rows, records, floats, classes))."""
    from app.exact import agenda

    k, ln = replay.constants(ch0, r)
    st = replay.state(ch0, r, ln)
    ws = replay.workspace(ch0, r, st)
    script, n1 = script_of(fc, d, ch0, steps)
    found = np.zeros((512, 4), dtype=np.int64)
    cap = 256
    qout, iout = np.zeros((cap, w.QCOLS), np.int64), np.zeros((cap, fr.NI), np.int64)
    fout, cout = np.zeros((cap, fr.NF), np.float64), np.zeros((cap, fr.NC), np.int64)
    nf = tree.follow(st, k, walk.WK(listing=tk.listing), tk, ws, script, n1, found, qout, iout, fout, cout)
    nq = int(found[-1, 0])
    agenda.finish(st, k)
    out = (found[:nf], st, script, n1)
    return out + ((qout[:nq], iout[:nq], fout[:nq], cout[:nq]),) if questions else out


def questions_on(fc_all, d, draws, forced=(), log=print) -> dict:
    """Pass 1 on `draws` (indices among the 512), the case's tables from the whole population (`fc_all`: the award
    partition is the case's, never a subset's): each draw's tree size, and the distinct questions as the judge reads
    them (the rendered key and situation class: `question_keys`), with the watched keys read (`depth`)."""
    import time

    from app.analysis.events import Chain
    from app.exact import oracle, question_keys

    fc, _ = oracle.context(tuple(draws))
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch0.instrument_cash()
    tk = case_tables(fc_all, d, ch0)
    qt = question_keys.case_tables(fc_all, d, ch0, tk)
    out: dict = {"draws": {}, "questions": set(), "reads": set()}
    for r, draw in enumerate(draws):
        t0 = time.perf_counter()
        q, nodes, leaves, deepest, reads, cls, dig, _ = explore(ch0, r, tk, qcap=4_000_000, forced=forced)
        if len(q) < len(dig) or len(q) >= 4_000_000:
            raise ValueError("more question occurrences than the buffer holds")
        _, first = np.unique(dig[:, 1], return_index=True)
        found = set()
        for j in first:
            row, code = q[j], cls[j]
            klass = "" if row[w.QN] == w.Q_VERDICT else question_keys.situation_class(row, code, qt)
            found.update((key, klass) for key in question_keys.keys(row, qt))
        out["questions"] |= found
        out["reads"] |= reads
        out["draws"][int(draw)] = {"nodes": nodes, "leaves": leaves, "deepest": deepest, "occurrences": len(q),
                                   "questions": len(found), "seconds": round(time.perf_counter() - t0, 1)}
        log(f"draw {draw}: {out['draws'][int(draw)]}")
    return out


def main(argv=None) -> None:
    """`python -m app.exact.pass1 <first> <last> <out.json>`: pass 1 on draws first..last (inclusive)."""
    import json
    import sys

    from app.exact import oracle

    first, last, path = (argv or sys.argv[1:])[:3]
    fc_all, d = oracle._forecaster()
    got = questions_on(fc_all, d, range(int(first), int(last) + 1), log=lambda s: print(s, flush=True))
    with open(path, "w") as fh:
        json.dump({"draws": got["draws"], "questions": sorted(got["questions"]), "reads": sorted(got["reads"])}, fh)


if __name__ == "__main__":
    main()
