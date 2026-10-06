"""Local, unbilled timing: uv run python tests/benchmark_decision_frontier.py.

One 512-draw state call versus the old walk's six candidate _trace calls,
including its prefix and financial caches. Setup is separate; both first-query
and repeated-query medians are reported. This is not a full-walk speedup.
"""
import json
import platform
import statistics
import time

import akoustis_20240514_fixture as fx
from test_dated_answer_domains import SAVED
from test_decision_frontier import cursors, seed
from test_ripe_after_levy import NONE, OFFER

from app.disputes.forecast import Forecaster, _Walk


def seconds(call):
    start = time.perf_counter()
    call()
    return time.perf_counter() - start


def main():
    print(platform.platform(), platform.processor())
    probes = (('post_trial_ruling', '', 'unchanged'), ('judgment_response', 'I1', 'none'),
              ('cash_floor', '1', 'neither'), ('cash_out', '', 'neither'),
              ('listing_date', 'compliance', ''), ('judgment_response', 'ripe', 'none'))
    for name, prefix in [('ripe96', SAVED[:8]), ('ruling74', NONE[:8]), ('payment_offer', OFFER[:9])]:
        start = time.perf_counter()
        chain = seed(prefix)
        state_setup = time.perf_counter() - start
        cur = cursors()
        start = time.perf_counter()
        fc = Forecaster([chain.d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                        hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
        walk = _Walk(fc, chain.d)
        # Seed the same answered prefix, outside the next-decision measurement.
        walk._trace(prefix)
        replay_setup = time.perf_counter() - start
        support = walk.mask_of(prefix)
        query = lambda chain=chain, cur=cur, support=support: chain.next_decisions(cur, support)  # noqa: E731
        first = seconds(query)
        warm = statistics.median(seconds(query) for _ in range(25))
        calls = 0

        def replays(walk=walk, prefix=prefix):
            nonlocal calls
            for probe in probes:
                walk._trace(prefix + (probe,))
                calls += 1

        replay_first = seconds(replays)
        replay_warm = statistics.median(seconds(replays) for _ in range(25))
        print(json.dumps(dict(root=name, draws=chain.n,
                              supported_draws=chain.n if support is None else int(support.sum()),
                              state_setup_s=state_setup,
                              frontier_first_s=first, frontier_warm_s=warm,
                              replay_setup_s=replay_setup, replay_first_s=replay_first,
                              replay_warm_s=replay_warm, trace_calls_per_selection=6,
                              total_measured_trace_calls=calls), sort_keys=True))


if __name__ == '__main__':
    main()
