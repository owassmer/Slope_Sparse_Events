"""Python object boundary for the Rust event-state machine.

Configuration and keyed draw generation retain their Python interfaces. Every
Chain transition/query is dispatched to NativeChain; an absent native method
raises rather than running the oracle. The native owner sits in a slot and
participates in garbage collection; serialized state rebuilds that owner.
"""
from __future__ import annotations

import inspect
from functools import wraps

from app import _native


def make_chain(reference):
    """Register the boundary after the reference interface has been defined."""
    cached = globals().get("RustChain")
    if cached is not None:
        return cached

    class RustChain(reference):
        __slots__ = ("_native_chain",)

        def __init__(self, *args, **kwargs):
            self._native_chain = _native.NativeChain(self.__dict__)
            reference.__init__(self, *args, **kwargs)
            self._bind_inputs()

        def _bind_inputs(self):
            if self.basis is not None and self.basis.line is not None:
                from app.analysis.engine import _kernel_line

                line = self.basis.line
                self._kernel_line = _kernel_line(line)
                self._ops_inflow = None if line.ops.inflow is None else line.ops.inflow[:, :line.days]
                self._ops_outflow = None if line.ops.outflow is None else line.ops.outflow[:, :line.days]
                self._ops_total = line.ops.total[:, :line.days]

        def __getstate__(self):
            return self.__dict__

        def __setstate__(self, state):
            self.__dict__.update(state)
            self._native_chain = _native.NativeChain(self.__dict__)
            self._bind_inputs()

        @classmethod
        def _from_state(cls, state: dict):
            out = cls.__new__(cls)
            out.__dict__.update(state)
            out._native_chain = _native.NativeChain(out.__dict__)
            out._bind_inputs()
            return out

    RustChain.__qualname__ = "RustChain"
    for name, original in vars(reference).items():
        if name.startswith("__"):
            continue
        if isinstance(original, property):
            setattr(RustChain, name, property(_method(name, original.fget, RustChain)))
        elif inspect.isfunction(original):
            setattr(RustChain, name, _method(name, original, RustChain))
    globals()["RustChain"] = RustChain
    return RustChain


def _method(name, original, base):
    signature = inspect.signature(original)
    parameters = tuple(signature.parameters.values())[1:]

    @wraps(original)
    def call(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        arguments = []
        for parameter in parameters:
            value = bound.arguments[parameter.name]
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                arguments.extend(value)
            elif parameter.kind == inspect.Parameter.VAR_KEYWORD:
                raise TypeError(f"Unexpected variadic native method: {name}")
            else:
                arguments.append(value)
        result = self._native_chain.call(name, *arguments)
        if name in ("clone", "sliced", "seen_at"):
            return self if result is self.__dict__ else base._from_state(result)
        return result

    return call


def __getattr__(name):
    if name != "RustChain":
        raise AttributeError(name)
    from app.analysis.events import PythonChain

    return make_chain(PythonChain)
