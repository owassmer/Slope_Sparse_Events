"""Selection of the deterministic execution backend.

The Rust extension is the production backend. ``python`` (or ``reference``)
selects the retained oracle for differential verification. ``auto`` is an
explicit opt-in for environments that have not built the extension yet.
"""
from __future__ import annotations

import importlib
import os
from functools import cache


@cache
def _extension():
    try:
        return importlib.import_module("app._native")
    except ImportError as exc:
        raise RuntimeError(
            "The Rust execution core is unavailable. Build the app._native extension "
            "before running, or set SLOPE_EXECUTION_BACKEND=python for the reference engine."
        ) from exc


def backend() -> str:
    value = os.environ.get("SLOPE_EXECUTION_BACKEND", "rust").lower()
    if value in ("python", "reference"):
        return "python"
    if value not in ("rust", "auto"):
        raise ValueError(f"Unknown SLOPE_EXECUTION_BACKEND: {value!r}")
    if value == "auto":
        try:
            _extension()
        except RuntimeError:
            return "python"
    return "rust"


def native_function(name: str):
    """Resolve an installed native function, or None for an explicit oracle run."""
    return getattr(_extension(), name) if backend() == "rust" else None


def status() -> dict:
    selected = backend()
    return {"backend": selected, "native": selected == "rust"}
