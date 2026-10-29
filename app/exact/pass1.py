"""Pass 1's Python side: the case's tables for the walk (`tree.TK`) and a draw's walk run (`tree.explore`)."""
from __future__ import annotations

import numpy as np

from app.analysis.events import MARKS
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
    conds = np.zeros(len(tree.QNODES), dtype=np.int64)
    for q, name in enumerate(tree.QNODES):
        if name in fc.spec:
            for c in wlk.situation_conditions(name):
                if c in MARKS:
                    conds[q] |= 1 << MARKS.index(c)
    ec = replay.export_equity.config(ch0)
    nc = replay.export_notes.config(ch0)
    return tree.TK(listing=ch0.fin is not None and ch0.fin.listing_deadline is not None, verdicts=verdicts,
                   reduced=reduced, conds=conds, bound=int(ec.bound), notice=int(nc.notice))


def explore(ch0, r: int, tk: tree.TK, budget: int = 10**9, qcap: int = 10**6):
    """Pass 1 on draw row r: (questions [n, QCOLS], nodes, leaves, deepest)."""
    k, ln = replay.constants(ch0, r)
    st = replay.state(ch0, r, ln)
    ws = replay.workspace(ch0, r, st)
    wk = walk.WK(listing=tk.listing)
    sk = tree.stack_for(st)
    out = np.zeros((qcap, tree.QCOLS), dtype=np.int64)
    nq, nodes, leaves, deepest = tree.explore(st, k, wk, tk, ws, sk, out, budget)
    return out[:min(nq, qcap)], nodes, leaves, deepest


G = {"pay": tree.G_PAY, "initiate_offering": tree.G_OFFER, "file": tree.G_FILE, "none": tree.G_NONE,
     "neither": tree.G_NONE}


def script_of(fc, d, ch0, steps):
    """A Python walk history's answers as the dated walk's elementary decisions: (script by decision id, the offering
    outcomes by occasion). A composite label stands for the conjunction the walk takes first where it names several
    (enforcement 'none': no request; 'set_aside': set aside outright)."""
    from app.analysis.events import plain
    from app.exact import fields_agenda as a
    from app.exact import fields_walk as w

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


def follow(fc, d, ch0, r: int, tk, steps):
    """The history `steps` followed on draw row r: (findings [n, 4], the state at the end)."""
    from app.exact import agenda

    k, ln = replay.constants(ch0, r)
    st = replay.state(ch0, r, ln)
    ws = replay.workspace(ch0, r, st)
    script, n1 = script_of(fc, d, ch0, steps)
    found = np.zeros((512, 4), dtype=np.int64)
    nf = tree.follow(st, k, walk.WK(listing=tk.listing), tk, ws, script, n1, found)
    agenda.finish(st, k)
    return found[:nf], st, script, n1
