"""Build a release wheel and atomically replace the checkout's native extension.

Replacing the file preserves an already loaded extension's inode. Fresh Python
processes load the new build; existing processes keep their current build.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def source_digest(root: Path) -> str:
    paths = [root / "pyproject.toml", root / "rust-toolchain.toml"]
    paths.extend(sorted((root / "native").glob("Cargo.*")))
    paths.extend(sorted((root / "native" / "src").rglob("*.rs")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    before = source_digest(root)
    with tempfile.TemporaryDirectory(prefix="slope-native-wheel-") as directory:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "maturin",
                "build",
                "--release",
                "--locked",
                "--interpreter",
                sys.executable,
                "--out",
                directory,
            ],
            cwd=root,
            check=True,
        )
        wheels = list(Path(directory).glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one native wheel, found {len(wheels)}")
        with zipfile.ZipFile(wheels[0]) as wheel:
            modules = [
                name
                for name in wheel.namelist()
                if name.startswith("app/_native.") and name.endswith((".so", ".pyd"))
            ]
            if len(modules) != 1:
                raise RuntimeError(f"expected one app._native extension, found {len(modules)}")
            target = root / "app" / Path(modules[0]).name
            fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as destination, wheel.open(modules[0]) as source:
                    shutil.copyfileobj(source, destination)
                    destination.flush()
                    os.chmod(temporary, 0o644)
                    os.fsync(destination.fileno())
                if source_digest(root) != before:
                    raise RuntimeError("Rust build inputs changed during compilation; rebuild before testing")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    print(f"Installed {target.relative_to(root)} atomically (source {before[:12]}).")
    print("Run verification in a fresh Python process to load this build.")


if __name__ == "__main__":
    main()
