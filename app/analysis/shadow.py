"""Shadow checks for the compiled fast paths. With SLOPE_SHADOW=1 each fast function also runs the Python it replaces
and raises on any difference, bit for bit; `counts` records how often each check ran and passed, so a check that never
fired is visible. Without the flag nothing here runs."""
from __future__ import annotations

import os

import numpy as np

ON = os.environ.get("SLOPE_SHADOW") == "1"
counts: dict[str, int] = {}


class ShadowMismatch(AssertionError):
    pass


def same(a, b) -> bool:
    """Bit-identical: arrays by dtype, shape and bytes; containers element by element; floats by their bits."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        if not (isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and a.dtype == b.dtype
                and a.shape == b.shape):
            return False
        if a.dtype == object:  # Python objects (strings): by value, not by the pointers the buffer holds
            return all(same(x, y) for x, y in zip(a.ravel().tolist(), b.ravel().tolist(), strict=True))
        return np.ascontiguousarray(a).tobytes() == np.ascontiguousarray(b).tobytes()
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return type(a) is type(b) and len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, (float, np.floating)) or isinstance(b, (float, np.floating)):
        return type(a) is type(b) and np.float64(a).tobytes() == np.float64(b).tobytes()  # type: ignore[arg-type]
    if type(a) is type(b) and hasattr(a, "__dict__") and type(a).__eq__ is object.__eq__:  # a plain object: its state
        return same(vars(a), vars(b))
    return type(a) is type(b) and a == b


def where(a, b, at: str = "") -> str:
    """The first place two values differ (a path into containers), for the mismatch message."""
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return f"{at} keys {sorted(map(repr, a.keys() ^ b.keys()))[:4]}"
        return next((where(a[k], b[k], f"{at}[{k!r}]") for k in a if not same(a[k], b[k])), at)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
        return next((where(x, y, f"{at}[{i}]") for i, (x, y) in enumerate(zip(a, b, strict=True)) if not same(x, y)), at)
    if type(a) is type(b) and hasattr(a, "__dict__") and type(a).__eq__ is object.__eq__:
        return where(vars(a), vars(b), at)
    return f"{at}: {type(a).__name__} {str(a)[:120]!r} vs {type(b).__name__} {str(b)[:120]!r}"


def check(name: str, fast, ref):
    """`fast`, after asserting it equals `ref` bit for bit (call only when ON)."""
    if not same(fast, ref):
        raise ShadowMismatch(f"shadow {name}: the fast path differs from the Python it replaces at {where(fast, ref)}")
    counts[name] = counts.get(name, 0) + 1
    return fast


def report() -> str:
    return "shadow checks passed: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none ran")
