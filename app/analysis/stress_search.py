"""Probability-independent search and a contractual exposure ceiling.

The ceiling is not an estimate from the search. Before funding an invoice a,
ceil(F * (C-collected) / C) + a <= limit. If R bounds C/F and
invoice_contract/a, the new balance owed is <= R * limit. Collections only
reduce it. C/F is a weighted average of the opening and invoice ratios.
Half-up fee rounding therefore matters; the nominal fee ratio alone is not a
ceiling. The same funding test is used by both net and daily processing.
"""
from __future__ import annotations

import time
from fractions import Fraction

import numpy as np

from app.analysis.core import stress_rows
from app.disputes.answer_first import AnswerFirstWalk, Guide
from app.disputes.forecast import combo_mask


def exposure_bound(line) -> np.ndarray:
    """Integer cents per operating draw, valid at every date on every history."""
    ex = line.setup.exposure
    opening = Fraction(ex.owed_cents, ex.principal_cents) if ex.principal_cents else Fraction(1)
    result = []
    for limits, routes in zip(line.limit, line.routes, strict=True):
        ratio = opening
        for amount in np.unique(routes):
            a = int(amount)
            if a > 0:
                contractual = a + (2 * a * line.setup.fee_bps + 10_000) // 20_000
                ratio = max(ratio, Fraction(contractual, a))
        ceiling = ratio * int(limits.max())
        result.append(max(ex.owed_cents, -(-ceiling.numerator // ceiling.denominator)))
    return np.array(result, dtype=np.int64)


def history_row(a, model, combo):
    """Never replay an answer on a draw that was not offered that history."""
    from app.analysis.events import EventCash, event_trace

    mask = combo_mask(combo, a.ops.draws)
    mask = np.ones(a.ops.draws, dtype=bool) if mask is None else mask
    if not mask.any():
        return None
    draws = a._draws.sub(mask)
    ev = EventCash.zeros(draws.n, a.days)
    for p in combo:
        ev = ev + event_trace(model.disputes[p.instance_id], p, a.setup, a.m, draws, a.sens).events
    row = stress_rows(draws.basis.line, a.opening, a.setup, [ev])[0]
    return row, mask


def rank(row):
    return row['uncollected_maturity_cents'], row['petition_at_peak']['stayed_claim_mean_cents']


def search(a, fc, model, borrower, *, samples=48, seed=731, seconds=420):
    """Uniform-answer chronological histories, each on its feasible draws, no judgment calls.

    Also evaluate the original walk's 25 largest conditional unrecovered means.
    Sampling has no probability cutoff.
    """
    from app.analysis.page import _worst, named, outcome_shares, parties, sequence
    from app.disputes.forecast import Forecaster

    started = time.monotonic()
    ranges = dict(fc.class_range)
    # Search nodes and facts are not part of the judged model or its page questions.
    fc = Forecaster(fc.disputes, fc.findings, borrower=fc.borrower, review=fc.review, horizon=fc.horizon,
                    hydrate=fc.hydrate, model=fc.m, setup=fc.setup, basis=fc.draws.basis,
                    sens=fc.sens, slots=fc.slots)
    best = None
    evaluated = 0
    draw_counts = []
    seen = set()

    def consider(combo):
        nonlocal best, evaluated
        key = tuple((p.instance_id, p.steps, p.mask) for p in combo)
        if key in seen:
            return
        seen.add(key)
        result = history_row(a, model, combo)
        if result is None:
            return
        row, mask = result
        evaluated += 1
        draw_counts.append(int(mask.sum()))
        if best is None or rank(row) > rank(best[0]):
            best = row, combo, mask

    shares = a.r.mask.mean(axis=1)
    conditional = np.divide(a.r.means['unrecovered'], shares,
                            out=np.full(len(shares), -np.inf), where=shares > 0)
    for i in np.argsort(conditional)[-25:]:
        consider(model.combos[i])
    sampled = 0
    stopped = False
    deadline = started + seconds

    class BoundedSearch(AnswerFirstWalk):
        def _loop(self, *args):
            nonlocal stopped
            if time.monotonic() >= deadline:
                stopped = True
                return  # discard unfinished prefixes; only chronological terminal histories are candidates
            return super()._loop(*args)

    # This case's chronological scheduler supports a pending liability claim.
    # Other dispute combinations are retained from the original walk above.
    for d, _ in fc.ordered():
        if d.stage != 'liability_pending' or d.borrower_role != 'debtor':
            continue
        guide = Guide(fc, lambda key, branches: dict.fromkeys(branches, 1 / len(branches)))
        walk = BoundedSearch(fc, d).configured(guide=guide, tau=2, replicates=samples, seed=seed)
        paths = walk.run()
        sampled += len(paths)
        others = [p for p in model.combos[0] if p.instance_id != d.instance_id]
        for p in paths:
            if time.monotonic() >= deadline + 90:
                stopped = True
                break
            consider((p, *others))
    row, combo, mask = best
    p = combo[0]
    d = model.disputes[p.instance_id]
    from copy import copy

    local = copy(fc)
    local.draws = fc.draws.sub(mask)
    tr = local.whole_trace(d, p.steps)
    text = sequence(p.steps, tr.day, tr.petition, a.setup.review, a.days, ranges | fc.class_range, tr.cause)
    shares = outcome_shares(p.steps, p.outcome, float(((tr.petition >= 0) & (tr.petition < a.days)).mean()),
                            tr.cause, tr.marks, a.days)
    worst = _worst([{**row, 'index': 0}], [max(shares, key=shares.get)], [0], [text], n=1)[0]
    worst.pop('index')  # this history need not have an index in the probability-weighted walk
    names = parties(borrower, d)
    worst.update(sequence=named(worst['sequence'], names), **{'class': named(worst['class'], names)},
                 draws=int(mask.sum()), draw_indices=np.flatnonzero(mask).tolist(),
                 figures_note='Unweighted means over only the draws on which this history is feasible.')
    bound = exposure_bound(a.line)
    return {
        'note': 'The search found this feasible history without using judgment probabilities. It is not claimed '
                'to be the worst there is. The bound is what no feasible history can exceed.',
        'worst_found': worst,
        'search': {'method': 'Time-limited uniform answers in the chronological walk; also evaluates the original walk’s 25 largest conditional unrecovered means. '
                             'Every candidate is evaluated only on its own feasible draws, unweighted.',
                   'seed': seed, 'samples_requested': samples, 'sampled_histories': sampled,
                   'walk_budget_seconds': seconds, 'evaluation_allowance_seconds': 90, 'budget_reached': stopped,
                   'histories_evaluated': evaluated, 'population_draws': a.ops.draws,
                   'history_draws_evaluated': sum(draw_counts),
                   'feasible_draws_per_history': {'min': min(draw_counts), 'max': max(draw_counts)},
                   'seconds': round(time.monotonic() - started, 2)},
        'bound': {'measure': 'Unrecovered at maturity and claim stayed at a petition (each separately)',
                  'per_draw_cents': bound.tolist(), 'mean_cents': float(bound.mean()),
                  'mean_note': 'The overall mean uses all operating draws. Compare a history only with the '
                               'bound on that history’s feasible draws, not with the overall mean.',
                  'any_feasible_subset_mean_cents': int(bound.max()),
                  'winner_draws_mean_cents': float(bound[mask].mean()),
                  'gap_to_found_mean_cents': float(bound[mask].mean()) - row['uncollected_maturity_cents'],
                  'gap_to_any_feasible_subset_mean_cents': int(bound.max()) - row['uncollected_maturity_cents'],
                  'rule': 'Maximum of opening contractual balance and highest daily limit times the largest '
                          'opening or routed-invoice contractual/principal ratio, rounded up to cents. '
                          'Includes whole-cent fee rounding; independent of event probabilities.'}}
