"""Package the reviewed design and acquired sources. No network or model calls."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "research/revision_v2"
OUT = DESIGN / "deliverables/Slope_Credit_Scenario_Research_and_Design_Kit.zip"
catalog = json.loads((DESIGN / "data/sources.json").read_text())
files = set()

for source in catalog["sources"]:
    path = ROOT / source["package_relative_path"]
    assert path.is_file(), path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"], path
    files.add(path)
    text_copy = path.with_suffix(".txt")
    if text_copy.exists() and text_copy.stat().st_size > 100:
        files.add(text_copy)

for dirname in ("contracts", "data", "reference"):
    for path in (DESIGN / dirname).rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix in (".md", ".json", ".py"):
            files.add(path)

for relative in (
    "research/revision_v2/README.md",
    "research/revision_v2/package_kit.py",
    "research/revision_v2/deliverables/Slope_Credit_Scenario_Build_Specification.md",
    "research/barfresh_case/acquired_sources.json",
    "research/barfresh_case/case_facts.json",
):
    files.add(ROOT / relative)

manifest = {
    "package": "Slope credit-event scenario research and design kit",
    "revision": "2",
    "status": "Research and design; not a built agent or lending application",
    "primary_catalog_records": len(catalog["sources"]),
    "warning": "Build dated agent evidence stores. Do not expose this entire kit, outcomes or evaluator answers to investigator/reviewer runtimes.",
    "files": [],
}
OUT.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as z:
    for path in sorted(files):
        raw = path.read_bytes()
        rel = path.relative_to(ROOT).as_posix()
        manifest["files"].append({"path": rel, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        z.writestr(rel, raw)
    z.writestr("README.md", (DESIGN / "README.md").read_text())
    z.writestr("PACKAGE_MANIFEST.json", json.dumps(manifest, indent=2) + "\n")

with zipfile.ZipFile(OUT) as z:
    assert z.testzip() is None
    for entry in manifest["files"]:
        assert hashlib.sha256(z.read(entry["path"])).hexdigest() == entry["sha256"]
print(json.dumps({"file": str(OUT), "bytes": OUT.stat().st_size, "packaged_files": len(files) + 2, "primary_catalog_records": len(catalog["sources"]), "zip_and_hash_checks": "passed"}))
