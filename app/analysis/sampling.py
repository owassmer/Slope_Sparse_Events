"""Each expected figure's range from the answer-first walk's sampled tail (design/002-answer-first.md).

The walk walks the likely histories exactly and, below its threshold, samples each set-aside point (a stub) in
independent groups of Latin-hypercube replicates, drawn from the judged distribution itself
(`app/disputes/answer_first.py`). For a stub s and group g, the group's observation of figure f is

    Z[s, g] = G_s x sum over the group's paths c of  w_c x v_c[f]

where w_c is the path's weight (probability over divisor, `EventModel.probs`), v_c its figure (a mean over the
draws, `page.path_scalars`) and G_s the stub's groups. The figure's estimate is the exactly walked paths plus, for
each stub, the mean of its groups. Because every sampled decision is drawn with its judged probability (checked here:
`check_masses`), on each draw the group's weights add up to the stub's mass m[s, d], so

    Z[s, g] lies in [A_s, B_s] = (1 / D) x sum over draws of m[s, d] x [lo[s, d], hi[s, d]]

where [lo, hi] are what any continuation of the stub can give the figure on that draw (`continuation_bounds`). The
groups of a stub are independent and identically distributed; the stubs are independent of each other.

The 99% range is then an empirical Bernstein bound on the sum over stubs, valid whatever the continuations hold:
- Bernstein: with probability at least 1 - delta_mean, |estimate - figure| <= t, where
  t = M L / 3 + sqrt((M L / 3)^2 + 2 V L), L = ln(2 / delta_mean), M = max_s (B_s - A_s) / G_s and
  V = sum_s Var(Z_s) / G_s;
- V is not known, so it is replaced by an upper bound that holds with probability at least 1 - delta_var: the
  unbiased sample variances' sum V_hat (each stub's term lies in [0, c_s^2 / (4 (G_s - 1))]) plus Hoeffding's
  sqrt(ln(1 / delta_var) / 2 x sum_s (c_s^2 / (4 (G_s - 1)))^2), and never above Popoviciu's sum_s c_s^2 / (4 G_s);
- delta_mean + delta_var = 1 - 0.99.
Taking the samples' own spread into account (rather than Hoeffding's worst case) narrows the range wherever the
continuations agree; the Popoviciu cap means it is never much wider than Hoeffding's.

Percentiles: the probability below any value has the same structure with each stub's term in [0, mass], so the
level at which a percentile is read is within `level_tolerance` (Hoeffding over the stubs, with a union over every
(day, bin) cell of the histograms the percentiles are read from).

After a judgment is changed the samples were drawn under other answers, so no sampling claim holds; the guaranteed
range is the exactly walked paths plus each stub's exact new mass times its floor and ceiling (`stub_variants` gives
what the page needs to compute it).
"""
from __future__ import annotations

import math

import numpy as np

CONFIDENCE = 0.99


def check_masses(stub: np.ndarray, group: np.ndarray, groups: np.ndarray, weights: np.ndarray, masks: np.ndarray,
                 prefix: np.ndarray) -> np.ndarray:
    """Each stub's mass on each draw, [stubs, draws], after checking that the walk sampled with the judged
    probabilities: on every draw a stub covers, each of its groups' paths' weights (times G_s) add up to the stub's
    mass there, the probability of its prefix under the judgments. That identity is what bounds a group's
    observation between the stub's floor and ceiling (module docstring), so a run where it fails stands as an error.

    stub, group: per path (-1: walked exactly); groups: G_s per stub; weights: per path (probability over divisor);
    masks: [paths, draws] bool; prefix: per path, the probability of its stub's prefix under the judgments."""
    n_stubs, draws = len(groups), masks.shape[1]
    mass = np.full((n_stubs, draws), np.nan)
    for i in np.flatnonzero(stub >= 0):
        s = stub[i]
        on = masks[i]
        seen = mass[s, on]
        if np.isfinite(seen).any() and not np.allclose(seen[np.isfinite(seen)], prefix[i], rtol=1e-9, atol=0):
            raise ValueError(f"stub {s}: paths covering the same draw disagree on the stub's prefix probability")
        mass[s, on] = prefix[i]
    mass = np.nan_to_num(mass, nan=0.0)
    for s in range(n_stubs):
        for g in range(groups[s]):
            sel = (stub == s) & (group == g)
            total = (weights[sel, None] * groups[s] * masks[sel]).sum(axis=0)
            if not np.allclose(total, mass[s], rtol=1e-9, atol=1e-15):
                bad = np.flatnonzero(~np.isclose(total, mass[s], rtol=1e-9, atol=1e-15))
                raise ValueError(f"stub {s} group {g}: weights do not add up to the stub's mass on draws "
                                 f"{bad[:8].tolist()} ({total[bad[:4]].tolist()} vs {mass[s, bad[:4]].tolist()}): "
                                 "the walk did not sample with the judged probabilities")
    return mass


def bernstein(z: list[np.ndarray], a: np.ndarray, b: np.ndarray, confidence: float = CONFIDENCE) -> float:
    """The half-width (module docstring) from each stub's group observations z[s] ([G_s]) and its range [a_s, b_s]."""
    delta = 1 - confidence
    d_mean, d_var = delta / 2, delta / 2
    c = np.maximum(b - a, 0.0)
    g = np.array([len(x) for x in z], dtype=np.float64)
    if (g < 2).any():
        raise ValueError("every stub needs at least two groups for its variance")
    var_hat = np.array([float(np.var(x, ddof=1)) for x in z])
    v_hat = float((var_hat / g).sum())
    term = c ** 2 / (4 * (g - 1))
    v_ub = min(v_hat + math.sqrt(math.log(1 / d_var) / 2 * float((term ** 2).sum())),
               float((c ** 2 / (4 * g)).sum()))
    big_l = math.log(2 / d_mean)
    m = float((c / g).max()) if len(c) else 0.0
    return m * big_l / 3 + math.sqrt((m * big_l / 3) ** 2 + 2 * v_ub * big_l)


def ranges(values: dict[str, np.ndarray], weights: np.ndarray, stub: np.ndarray, group: np.ndarray,
           groups: np.ndarray, mass: np.ndarray, bounds: dict[str, tuple[np.ndarray, np.ndarray]],
           confidence: float = CONFIDENCE) -> dict[str, dict]:
    """Per figure: the estimate, its 99% range and how it was reached.

    values: figure -> per path value (a mean over the draws); weights: per path; stub, group: per path;
    groups: G_s; mass: [stubs, draws] (`check_masses`); bounds: figure -> ([stubs, draws] floor, ceiling), or absent
    where no ceiling holds (that figure gets no range)."""
    draws = mass.shape[1]
    head = stub < 0
    out = {}
    for f, v in values.items():
        x = weights * v
        estimate = float(x[head].sum())
        if f not in bounds:
            out[f] = {"estimate": estimate + float(x[~head].sum()), "range": None,
                      "note": "no ceiling holds for this figure, so its sampled part has no range"}
            continue
        lo, hi = bounds[f]
        a = (mass * lo).sum(axis=1) / draws
        b = (mass * hi).sum(axis=1) / draws
        z = []
        for s in range(len(groups)):
            zs = np.array([groups[s] * float(x[(stub == s) & (group == g)].sum()) for g in range(groups[s])])
            if ((zs < a[s] - 1e-6 * max(1.0, abs(a[s]))) | (zs > b[s] + 1e-6 * max(1.0, abs(b[s])))).any():
                raise ValueError(f"{f}: stub {s}'s observations {zs.tolist()} leave its range [{a[s]}, {b[s]}]: "
                                 "a continuation went beyond its floor or ceiling")
            z.append(zs)
            estimate += float(zs.mean())
        half = bernstein(z, a, b, confidence) if z else 0.0
        out[f] = {"estimate": estimate, "range": [estimate - half, estimate + half], "half_width": half,
                  "guaranteed": [float(x[head].sum() + a.sum()), float(x[head].sum() + b.sum())]}
    return out


def level_tolerance(mass: np.ndarray, groups: np.ndarray, cells: int, confidence: float = CONFIDENCE) -> float:
    """How far the probability below any value can be from the estimate's, at the stated confidence, over every one of
    `cells` (day, bin) cells at once: Hoeffding over the stubs, each group's term in [0, the stub's mean mass]."""
    m = mass.sum(axis=1) / mass.shape[1]
    big_l = math.log(2 * max(cells, 1) / (1 - confidence))
    return math.sqrt(big_l / 2 * float((m ** 2 / np.maximum(groups, 1)).sum()))
