"""Rebuilding must preserve a running process's mapped extension image."""
from __future__ import annotations

import importlib.util
import mmap
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def builder(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_native.py"
    spec = importlib.util.spec_from_file_location("native_builder", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative in ("scripts", "native/src", "app"):
        (tmp_path / relative).mkdir(parents=True)
    for relative in ("pyproject.toml", "rust-toolchain.toml", "native/Cargo.toml", "native/src/lib.rs"):
        (tmp_path / relative).write_text("original input\n")
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts" / "build_native.py"))

    def build(command, **kwargs):
        output = Path(command[command.index("--out") + 1])
        with zipfile.ZipFile(output / "slope.whl", "w") as wheel:
            wheel.writestr("app/_native.test.so", b"new native image")

    monkeypatch.setattr(module.subprocess, "run", build)
    return module, tmp_path


def test_atomic_native_install_preserves_existing_memory_map(builder):
    module, root = builder
    target = root / "app" / "_native.test.so"
    target.write_bytes(b"original native image")
    original_inode = target.stat().st_ino
    with target.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as image:
        module.main()
        assert image[:] == b"original native image"
        assert target.read_bytes() == b"new native image"
        assert target.stat().st_ino != original_inode


def test_changed_native_sources_reject_install_and_preserve_previous_image(builder, monkeypatch):
    module, root = builder
    target = root / "app" / "_native.test.so"
    target.write_bytes(b"original native image")
    build = module.subprocess.run

    def changed(command, **kwargs):
        build(command, **kwargs)
        (root / "native" / "src" / "lib.rs").write_text("changed during compilation\n")

    monkeypatch.setattr(module.subprocess, "run", changed)
    with pytest.raises(RuntimeError, match="changed during compilation"):
        module.main()
    assert target.read_bytes() == b"original native image"
    assert list((root / "app").iterdir()) == [target]
