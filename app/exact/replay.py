"""Python preparation for the dated walk on one draw (`app/exact/agenda.py`): its constants (`K`), its state at the
review date (`St`, from a Python Chain after `instrument_cash`), and a history's steps encoded as integers."""
from __future__ import annotations

import numpy as np

from app.analysis import baby_shelf
from app.analysis.events import (
    DISTRESS_BOOKINGS,
    pending_template,
    plain,
    pval,
    settlement_terms,
    verdict_amount,
)
from app.exact import agenda, cash, core, export_equity, export_notes
from app.exact import fields as f
from app.exact import fields_agenda as a
from app.exact import fields_notes as n

OCCASION_IDS = {name: i for i, name in enumerate(a.OCCASIONS)}
DEFAULT_BRANCH = {"no": n.NO, "yes": n.YES, "holders_file": n.HOLDERS_FILE, "accelerated": n.ACCELERATED}
DELIST_BRANCH = {"none": 0, "accelerated": 1, "repurchase_unpaid": 2, "petition_delist": 3,
                 "petition_delist_holders": 4, "petition_repurchase": 5, "petition_repurchase_holders": 6}
YES_NO = {"no": a.NO, "yes": a.YES, "denied": a.DENIED}
BOOKING = {"pay": a.B_PAY, "seek": a.B_SEEK, "petition": a.B_PETITION, "offer": a.B_OFFER, "none": a.B_NONE}  # respond


def constants(ch, r: int) -> agenda.K:
    """Row r of the chain's draws: the case's constants on that draw."""
    m, s, sens, p = ch.m, ch.s, ch.sens, ch.p
    if not (ch.pending and ch.daily and ch.equity):
        raise NotImplementedError("the dated walk: a pending claim under daily processing and the equity model")
    case = core.Case.of(ch)
    share = (s.collateral_share[0] if s.collateral_share else
             (m["parameters"]["bond_collateral_share_bps"]["lower"] if sens.get("bond_collateral_share_bps")
              else m["parameters"]["bond_collateral_share_bps"]["value"]) / 10_000)
    dates = export_notes.listing_dates(ch)
    ln = cash.line_for(ch, r)
    mode, count = settlement_terms(m, sens)
    if mode != "installments":
        raise NotImplementedError(f"settlement terms {mode}: the dated walk books installments")
    return agenda.K(
        N=int(ch.N), bps=int(ch.bps), pending=True, frcp=case.frcp_50b_59, frap=case.frap_4a1a, frcp62=case.frcp_62a,
        briefing=case.briefing_days, next_bd=case.entry_next_business_day, nbd=int(core.entry_days(ch)[r]),
        sensitive_lag=case.sensitive_lag, DC=np.ascontiguousarray(core.draw_constants(ch)[r]),
        levy_lag=int(p("levy_lag_days")), petition_lag=int(p("petition_lag_days")),
        bond_years=float(pval(m, "bond_forward_interest_years", sens.get("bond_forward_interest_years", False))),
        bond_share=float(share), zero_floor=bool(sens.get("cash_floor")), ec=export_equity.config(ch),
        nc=export_notes.config(ch), sched=export_equity.atm_schedule(ch),
        lower=baby_shelf.year_start(ch, np.arange(ch.N)).astype(np.int64), ldates=dates,
        rep_cal=export_notes.repurchase_calendar(ch, [int(dates[n.DELIST_SUSP]), int(dates[n.DELIST_PANEL])]),
        legal=np.ascontiguousarray(ch.basis.legal[r], dtype=np.int64), opening=int(ch.basis.opening),
        ops_in=ln.ops_in, need=np.ascontiguousarray(ch.basis.need[r], dtype=np.int64),
        noncash=p("stay_security") == "noncash", claimed=int(ch.claimed()),
        settle_lag=int(m["parameters"]["settlement_date_in_interval"]["value"]),
        settle_end=bool(sens.get("settlement_date_in_interval")), settle_count=count,
        months=np.array([[ch.months_after(np.array([t]), q)[0] for q in range(count)] for t in range(ch.N)],
                        dtype=np.int64).reshape(ch.N, count), court=core.court_table(ch)), ln


def state(ch, r: int, ln) -> agenda.St:
    """The draw's state as the chain holds it (at the review date: after `instrument_cash`)."""
    S, D, P, takes, offers, atm, answers, parts, coupons = export_notes.notes_state(ch, r, OCCASION_IDS)
    if len(ch.stays) or ch.waiting or ch._pending_writs or len(parts):
        raise ValueError("the dated walk starts from the review date's state: no step walked")
    S[a.N_WRITS:a.WRITS0 + a.MAX_WRITS] = 0
    for k in range(1, a.MAX_FLOORS + 1):
        S[a.FLOOR0 + k] = f.BIG
    S[a.CASH_FIRST_UNPAID] = S[a.CASH_NONPAYMENT] = S[a.PROSPECTIVE] = f.BIG
    S[a.N_PARTS] = 0
    return agenda.St(S=S, D=np.ascontiguousarray(D), P=np.ascontiguousarray(P), takes=takes, offers=offers, atm=atm,
                     answers=answers, parts=np.zeros((a.MAX_PARTS, 2), np.int64), coupons=coupons,
                     wait=np.zeros((a.MAX_WAIT, a.WCOLS), np.int64), stays=np.zeros((a.MAX_STAYS, a.TCOLS), np.int64),
                     rec=np.zeros((a.MAX_STEPS, 4), np.int64), steps=np.zeros((a.MAX_STEPS, a.STEP_COLS), np.int64),
                     scratch=np.zeros(len(export_equity.atm_schedule(ch)), np.int64),
                     before=np.zeros((4, ch.N), np.int64), lockbuf=np.zeros(ch.N, np.int64), ln=ln)


def workspace(ch, r: int, st: agenda.St) -> agenda.WS:
    """A projection's workspace on the draw: the state at the review date and a view with its own line buffers."""
    init = agenda.St(*(x.copy() if isinstance(x, np.ndarray) else x for x in st))
    view = agenda.St(*(x.copy() if isinstance(x, np.ndarray) else x for x in st[:-1]), cash.line_for(ch, r))
    return agenda.WS(init=init, scratch=view)


def encode(ch, step) -> np.ndarray:
    """A history's step (node, ctx, branch) as the integers the compiled walk reads."""
    node, ctx, branch = step
    b = plain(branch)
    out = np.zeros(a.STEP_COLS, dtype=np.int64)
    out[0] = a.NODES.index(node)
    if node == "verdict":
        if b.startswith("award:"):
            _, total, lo, hi = b.split(":")
            out[2:7] = a.V_AWARD, int(total), int(lo), 0 if hi == "top" else int(hi), hi == "top"
        else:
            spec = pending_template(ch.m)["verdict_branches"][b]
            amount = verdict_amount(ch.d, ch.m, b, ch.sens) if spec["judgment"] else 0
            out[2:7] = a.V_BRANCH, bool(spec["judgment"]), amount, bool(spec.get("adverse")), list(
                pending_template(ch.m)["verdict_branches"]).index(b)
    elif node == "post_trial_ruling":
        if b.startswith("reduced:"):
            out[2], out[3] = a.R_REDUCED, int(b.split(":")[1])
        else:
            out[2] = {"unchanged": a.R_UNCHANGED, "set_aside": a.R_SET_ASIDE}[b]
    elif node == "settle":
        out[1], out[2] = a.SETTLE_CTX.index(ctx), YES_NO[b]
    elif node in ("stay", "registration_early"):
        out[1], out[2] = a.PHASE_CTX.index(ctx), YES_NO[b]
    elif node in ("post_trial_motions", "execute_pre_ruling", "appeal"):
        out[2] = YES_NO[b]
    elif node == "enforce":
        out[2] = {"none": a.E_NONE, "levy": a.E_LEVY}[b]
    elif node == "listing":
        out[2] = {"compliant": a.L_COMPLIANT, "hearing": a.L_HEARING, "suspended": a.L_SUSPENDED}[b]
    elif node == "delisting_notes":
        out[2] = DELIST_BRANCH[b]
    elif node == "judgment_default":
        out[1], out[2] = a.JDEFAULT_CTX.index(ctx), DEFAULT_BRANCH[b]
    elif node == "judgment_response":
        out[1], out[2] = a.RESPONSE_CTX.index(ctx), BOOKING[ch.booking(node, b)]
    elif node in ("cash_floor", "cash_out"):
        out[1] = int(ctx) if node == "cash_floor" else 0
        out[2] = BOOKING[DISTRESS_BOOKINGS[b]]
    elif node == "offering":
        out[1], out[2] = OCCASION_IDS[ctx], YES_NO[b]
    elif node == "nonpayment":
        out[2] = {"due": a.NP_DUE, "petition": a.NP_PETITION, "holders_file": a.NP_HOLDERS}[b]
    else:
        raise NotImplementedError(f"no encoding for {node}")
    return out
