"""The answer-first ranges (app/analysis/sampling.py): the mass identity, and coverage that holds whatever the
continuations hold, including a rare loss no group happens to see."""
import numpy as np
import pytest

from app.analysis import sampling


def test_check_masses_finds_the_stub_mass_and_rejects_a_sampling_that_is_not_the_judgment():
    # one stub, two groups of one path each covering both draws; prefix 0.02; weight x G = 0.02
    stub, group, groups = np.array([-1, 0, 0]), np.array([-1, 0, 1]), np.array([2])
    masks = np.ones((3, 2), dtype=bool)
    prefix = np.array([0.0, 0.02, 0.02])
    weights = np.array([0.98, 0.01, 0.01])
    np.testing.assert_allclose(sampling.check_masses(stub, group, groups, weights, masks, prefix), [[0.02, 0.02]])
    with pytest.raises(ValueError, match="did not sample with the judged probabilities"):
        sampling.check_masses(stub, group, groups, np.array([0.98, 0.012, 0.01]), masks, prefix)


def _trial(rng, stubs, groups, rare):
    """A synthetic tail: each stub's continuation is worth 1.0 per unit mass, except a rare one worth 0.0 with
    probability `rare`; floor 0, ceiling 1. Returns the estimate's error and the half-width."""
    mass = np.full((stubs, 1), 0.001)
    z = [mass[s, 0] * (rng.random(groups) >= rare).astype(float) for s in range(stubs)]
    a, b = np.zeros(stubs), mass[:, 0]
    truth = float(mass.sum() * (1 - rare))
    estimate = sum(float(x.mean()) for x in z)
    return abs(estimate - truth), sampling.bernstein(z, a, b)


@pytest.mark.parametrize("rare", [0.0, 0.02, 0.3])
def test_the_range_covers_at_least_its_confidence(rare):
    rng = np.random.default_rng(5)
    misses = sum(err > half for err, half in (_trial(rng, 60, 3, rare) for _ in range(2000)))
    assert misses <= 20  # 99% of 2,000: at most 1% missed (the bound is conservative, so usually none)


def test_a_rare_loss_no_group_sees_still_leaves_a_range():
    rng = np.random.default_rng(1)
    err, half = _trial(rng, 10, 2, 0.001)  # almost surely every group sees only the common value
    assert half > 0.0


def test_the_range_is_never_much_wider_than_hoeffding():
    z = [np.array([0.0, 1.0]) * 1e-3 for _ in range(50)]
    a, b = np.zeros(50), np.full(50, 1e-3)
    hoeffding = np.sqrt(np.log(2 / 0.005) / 2 * (b ** 2 / 2).sum())
    assert sampling.bernstein(z, a, b) <= 1.2 * hoeffding


def test_ranges_rejects_an_observation_outside_its_floor_and_ceiling():
    stub, group, groups = np.array([0, 0]), np.array([0, 1]), np.array([2])
    mass = np.array([[0.02]])
    bounds = {"collected": (np.array([[0.0]]), np.array([[100.0]]))}
    ok = sampling.ranges({"collected": np.array([50.0, 60.0])}, np.array([0.01, 0.01]), stub, group, groups, mass,
                         bounds)
    assert ok["collected"]["range"][0] <= ok["collected"]["estimate"] <= ok["collected"]["range"][1]
    with pytest.raises(ValueError, match="leave its range"):
        sampling.ranges({"collected": np.array([50.0, 600.0])}, np.array([0.01, 0.01]), stub, group, groups, mass,
                        bounds)


def test_a_changed_judgment_moves_each_stubs_mass_exactly_and_nothing_else():
    """core.EventModel._weigh: a sampled continuation keeps the run's answers after its stub's prefix, so a changed
    answer inside the stub cannot push a figure outside its range (the code review's case: a filing probability of
    1.009 from two samples reweighted to 0.99)."""
    from app.analysis.core import EventModel
    from app.disputes.forecast import DisputePath, Judgment

    def judged(key, dist):
        return Judgment(key=key, instance_id="d", node=key, question_id=key, event=key, assumptions=(), window="",
                        distribution=dist, observation_id="stand-in")

    head = DisputePath(instance_id="d", steps=(), outcome="", edges=(("q1", "no"),))
    sampled = [DisputePath(instance_id="d", steps=(("x", "", str(g)),), outcome="", edges=(("q1", "yes"), ("q2", "a")),
                           divisor=0.5 * 2, stub=0, group=g) for g in (0, 1)]
    model = EventModel({}, {"q1": judged("q1", {"yes": 0.009, "no": 0.991}), "q2": judged("q2", {"a": 0.5, "b": 0.5})},
                       {}, [], combos=[(head,)] + [(p,) for p in sampled],
                       stubs={"d": [{"edges": 1, "groups": 2, "group_size": 1}]})
    np.testing.assert_allclose(model.probs(), [0.991, 0.0045, 0.0045])
    np.testing.assert_allclose(model.probs({"q2": {"a": 0.99, "b": 0.01}}), [0.991, 0.0045, 0.0045])
    np.testing.assert_allclose(model.probs({"q1": {"yes": 0.5, "no": 0.5}}), [0.5, 0.25, 0.25])
