"""The Akoustis pre-D record (app/disputes/akoustis_pre_d.py) and the operating basis the tests share."""

from __future__ import annotations

from app.analysis import operating
from app.analysis.engine import NEED_DAYS, prepare
from app.analysis.events import Basis
from app.analysis.setup import DRAWS, SEED, Setup
from app.disputes.akoustis_pre_d import CLOSE, COMPONENTS, MOTIONS, NOTES, REVIEW, SETUP, SNAP, judgment
from app.finance.bank import load_feed

__all__ = ["CLOSE", "COMPONENTS", "MOTIONS", "NOTES", "REVIEW", "SETUP", "SNAP", "basis", "judgment"]


def basis(setup: Setup = SETUP) -> tuple[object, Basis]:
    feed = load_feed(SNAP)
    days = (setup.horizon - setup.review).days
    ops = operating.simulate(feed, days + NEED_DAYS, DRAWS, SEED, setup.variability)
    line = prepare(setup, ops)
    return feed, Basis.of(ops, line.need, feed.available_cents)
