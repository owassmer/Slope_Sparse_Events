"""002: one full answer-first run of the real case, as `slope run` makes it, and what its page claims: time by
stage, the walk's size, the mass identity (checked inside the build: a failure stops it), and every expected figure's
estimate and 99% range against 1% of the commitment, the percentiles' level tolerance, the worst history found."""
import json
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
t0 = time.monotonic()
proc = subprocess.Popen(["uv", "run", "slope", "run", "akoustis_20240514", *args], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
tail = []
for line in proc.stdout:  # as it goes, so a line stopped at its limit still shows how far it got
    tail = (tail + [line])[-60:]
    if any(w in line for w in ("tree:", "walk", "analysis", "stress search", "answer_first-", "precision", "Error",
                               "error", "Traceback", "stub", "simulate", "bins")):
        print(f"  [{time.monotonic() - t0:5.0f}s] " + line.rstrip()[:300], flush=True)
code = proc.wait()
print(f"slope run {' '.join(args)}: exit {code} in {time.monotonic() - t0:.0f} s", flush=True)
if code:
    print("".join(tail)[-4000:])
    sys.exit(1)
run = sorted(Path("var/runs").glob("akoustis_20240514-answer_first-*"))[-1]
page = json.loads((run / "page.json").read_text())
Path("var/diag/002-verify").mkdir(parents=True, exist_ok=True)
(Path("var/diag/002-verify") / f"page-{'_'.join(args) or 'default'}.json").write_text(json.dumps(page))
meta = page["meta"]
limit = meta.get("limit_cents")
print(f"commitment (limit) ${limit / 100:,.0f}; 1% = ${limit / 1e4:,.0f}; judgments: {meta.get('judgments')}")
print(f"paths {len(page['paths']['edges'])}, stubs {len(page.get('stubs', []))}, level tolerance "
      f"{meta.get('level_tolerance')}")
for k, r in sorted(meta.get("ranges", {}).items()):
    if r.get("range"):
        w = r["range"][1] - r["range"][0]
        unit = "" if k == "petition_p" else f" = {100 * w / limit:.2f}% of commitment"
        print(f"  {k:18s} estimate {r['estimate']:>16,.2f} range width {w:>14,.2f}{unit}; guaranteed "
              f"{r['guaranteed'][0]:,.0f}..{r['guaranteed'][1]:,.0f}")
    else:
        print(f"  {k:18s} estimate {r['estimate']:>16,.2f} no range: {r.get('note')}")
s = page.get("stress", {})
if s:
    print(f"worst found: {s['worst_found']['sequence'][:200]}; unrecovered {s['worst_found']['unrecovered']}; "
          f"ceiling on its draws {s['bound']['winner_draws_mean_cents']:,.0f}")
