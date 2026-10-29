"""The fidelity harness for the exact evaluator (design/002-exact-evaluator.md, Fidelity): random complete histories
of the real case, and what today's Python computes for each of them on each draw, which every compiled component is
checked against field by field.

The Python is the definition while both exist. A history here is a `DisputePath` the chronological walk emits (so
every answer is one its question offers on its date, on the draws the path follows); its reference is
`events.event_trace` on those draws: the event cash by kind and day, the petition day and its cause, the marks, and
per step the decision day, the cash, the amount owed and the collateral the Python records.

    histories(n, draws, seed)  -> n random complete histories, each with the draws it follows
    reference(history, draws)  -> {draw index: {field: array or int}} on the draws the history follows

`context(draws)` is the real case (the lead run's investigation, the case's setup and model) on the chosen draws.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import numpy as np


@cache
def _run_context():
    from pathlib import Path

    from app.analysis.build import run_context
    from app.analysis.walk_measurement import LEAD_RUN
    from app.disputes.parallel import _variant

    ctx = run_context(LEAD_RUN, Path("runs/recorded"))
    setup, sens = _variant(ctx)
    return ctx, setup, sens


def _forecaster():
    """A fresh Forecaster of the real case (its caches are its own: one shared across draw sets would serve a prefix
    traced on other draws)."""
    from app.analysis.build import basis_for
    from app.analysis.population_walk import forbidden
    from app.disputes.forecast import Forecaster

    ctx, setup, sens = _run_context()
    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=forbidden, setup=setup, basis=basis_for(ctx["feed"], setup),
                    slots=ctx["slots"], model=ctx["m"], sens=sens)
    d = next(d for d, _ in fc.ordered() if d.stage == "liability_pending" and d.borrower_role == "debtor")
    return fc, d


@cache
def _full_context():
    return _forecaster()


def context(draws: tuple[int, ...]):
    """The real case's Forecaster on the chosen operating draws (indices among the 512), and its pending dispute,
    built fresh. The draws keep their own identities (`Draws.sub`), so a draw's operating flows, lags and verdict day
    are the same here as in the full run."""
    fc, d = _forecaster()
    keep = np.zeros(fc.draws.n, dtype=bool)
    keep[list(draws)] = True
    fc.draws = fc.draws.sub(keep)
    return fc, d


@dataclass(frozen=True)
class History:
    path: object          # forecast.DisputePath
    draws: tuple[int, ...]  # the draws (indices among the 512) it follows


# the decisions whose answers end the dispute's walk, and those answers (plain form): settling, paying, filing for
# protection, the ruling setting the verdict aside
ENDING = {"settle": ("yes",), "settlement_accept": ("yes",), "judgment_response": ("pay", "file"), "debtor_response": ("pay", "file"),
          "financing_at_floor": ("file",), "petition_cash_floor": ("yes", "file"), "petition_cash_out": ("yes", "file"),
          "petition_on_notes": ("yes",), "post_trial_ruling": ("set_aside",)}


def _long(key: str, branches: tuple[str, ...]) -> dict[str, float]:
    """A guide that carries the dispute on: an answer that ends it (settling, paying, filing, the verdict set aside)
    a tenth as likely as one that does not, every other question's answers equally likely, so histories reach the
    deep parts of the tree (levies, notes, listing, the cash floor)."""
    from app.analysis.events import plain

    node = key.split(":", 1)[-1].split("|", 1)[0]
    end = ENDING.get(node, ())
    w = {b: (0.1 if plain(b) in end else 1.0) for b in branches}
    if node == "verdict_finding" and "yes" in w:  # the jury's findings for the claimant: an award, so a judgment
        w["yes"] = 9.0
    total = sum(w.values())
    return {b: v / total for b, v in w.items()}


def histories(n: int, draws: tuple[int, ...], seed: int = 0, style: str = "uniform") -> list[History]:
    """n random complete histories of the real case on `draws`: the chronological walk run with every offered answer
    equally likely ("uniform") or with the answers that end the dispute made rare ("long", to reach deep paths),
    sampled (never pruned by probability), each history kept with the draws it follows."""
    from app.disputes.answer_first import AnswerFirstWalk, Guide
    from app.disputes.forecast import path_mask

    fc, d = context(draws)
    guide = Guide(fc, _long if style == "long" else (lambda key, branches: dict.fromkeys(branches, 1 / len(branches))))
    walk = AnswerFirstWalk(fc, d).configured(guide=guide, tau=2, groups=n, min_groups=n, group_size=1,
                                             enumerate_days=0, seed=seed)
    out = []
    ids = np.array(sorted(draws))
    for p in walk.run():
        m = path_mask(p, fc.draws.n)
        on = ids if m is None else ids[m]
        if len(on):
            out.append(History(p, tuple(int(x) for x in on)))
    return out


def reference(h: History, draws: tuple[int, ...] | None = None) -> dict[int, dict]:
    """What today's Python computes for history `h` on each of its draws (or the given subset of them)."""
    from app.analysis.events import event_trace

    on = tuple(h.draws) if draws is None else tuple(x for x in draws if x in h.draws)
    fc, d = context(on)
    tr = event_trace(d, h.path, fc.setup, fc.m, fc.draws, fc.sens)
    ev = tr.events
    out = {}
    for i, draw in enumerate(on):
        rec = {"cash": ev.cash[i].copy(), "lock": ev.lock[i].copy(), "capacity": ev.capacity[i].copy(),
               "petition": int(ev.petition[i]), "cause": int(tr.cause[i]) if tr.cause is not None else -1,
               "marks": {k: int(v[i]) for k, v in tr.marks.items()},
               "kinds": {k: v[i].copy() for k, v in (ev.kinds or {}).items()},
               "incurred": {k: int(v[i]) for k, v in (ev.incurred or {}).items()},
               "proceeds": {k: int(v[i]) for k, v in (ev.proceeds or {}).items()},
               "steps": [{"day": int(day[i]), "cash": int(cash[i]), "owed": int(owed[i]), "collateral": int(col[i])}
                         for day, cash, owed, col in zip(tr.day, tr.cash, tr.owed, tr.collateral, strict=True)]}
        out[draw] = rec
    return out
