"""The compiled walk. Its numba cache is keyed on the whole package's source: numba checks only a cached function's own
file, so a change to a function it calls in another module would otherwise leave the caller running the old callee
(a stale class code was found this way, 3 November 2026). Each source state compiles into its own directory under
var/numba/; the others are removed."""
import hashlib
import shutil
from pathlib import Path

import numba

_HERE = Path(__file__).resolve().parent
_KEY = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(_HERE.glob("*.py")))).hexdigest()[:16]
_ROOT = _HERE.parent.parent / "var" / "numba"
numba.config.CACHE_DIR = str(_ROOT / _KEY)
if _ROOT.exists():
    for old in _ROOT.iterdir():
        if old.name != _KEY and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
