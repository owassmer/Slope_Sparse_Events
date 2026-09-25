"""Command-line entry point. Subcommands are added by each build step."""

import typer

cli = typer.Typer(no_args_is_help=True, help="Slope external-event credit scenario module")


@cli.command()
def version() -> None:
    """Print the package version."""
    from importlib.metadata import version as _v

    typer.echo(_v("slope-sparse-events"))


@cli.command()
def smoke(
    jev: bool = typer.Option(True, help="Include the one budgeted Jev call (separately billed)."),
) -> None:
    """Step 1 runtime smoke test: subscription auth, scoped tool + schema output, Jev, reviewer sign-in."""
    from app.agent.smoke import run_smoke

    record = run_smoke(with_jev=jev)
    typer.echo(f"{record['status']}  ->  runs/recorded/runtime_smoke.json")
    if record.get("error") or record.get("failed_checks"):
        typer.echo(f"  {record.get('error') or record['failed_checks']}")
    raise typer.Exit(0 if record["status"] == "PASS" else 1)


evidence = typer.Typer(no_args_is_help=True, help="Dated evidence snapshots (step 2)")
cli.add_typer(evidence, name="evidence")


@evidence.command("build")
def evidence_build(snapshot: str = typer.Argument("all", help="Snapshot ID or 'all'.")) -> None:
    """Verify source hashes and build the snapshot database(s) under var/evidence/."""
    from app.evidence import SNAPSHOTS
    from app.evidence.snapshot import build_snapshot

    for sid in SNAPSHOTS if snapshot == "all" else (snapshot,):
        m = build_snapshot(sid)
        typer.echo(f"{sid}: cutoff {m['cutoff']}  manifest {m['evidence_manifest_hash'][:12]}")
        for src, c in m["counts"].items():
            typer.echo(f"  + {src}: {c['sections']} sections, {c['tables']} tables")
        for x in m["excluded"]:
            typer.echo(f"  - {x['source_id']}: {x['reason']}")


@evidence.command("search")
def evidence_search(snapshot: str, query: str, limit: int = 8) -> None:
    """Search a snapshot as the investigation tools will."""
    from app.evidence.store import EvidenceStore

    for r in EvidenceStore(snapshot).search(query, limit=limit):
        typer.echo(f"{r['id']}  [{r['kind']}]  {' > '.join(r['heading_path'])}")
        typer.echo(f"    {r['snippet']}")


@evidence.command("read")
def evidence_read(snapshot: str, item_id: str) -> None:
    """Print a section or table with its context and source metadata."""
    import json

    from app.evidence.store import EvidenceStore

    typer.echo(json.dumps(EvidenceStore(snapshot).read(item_id), indent=2, default=str))


finance = typer.Typer(no_args_is_help=True, help="Deterministic finance core (step 3)")
cli.add_typer(finance, name="finance")


@finance.command("check")
def finance_check() -> None:
    """Reproduce the kit's reference arithmetic with the finance engine."""
    from app.finance.reference_check import checks

    bad = 0
    for name, engine, reference in checks():
        ok = engine == reference
        bad += not ok
        typer.echo(f"{'ok ' if ok else 'MISMATCH'}  {name}: {engine}" + ("" if ok else f" (reference {reference})"))
    typer.echo(f"{'All reference values reproduced' if not bad else f'{bad} mismatches'}")
    raise typer.Exit(1 if bad else 0)


jev_cli = typer.Typer(no_args_is_help=True, help="Jev semantic layer (step 4a)")
cli.add_typer(jev_cli, name="jev")


@jev_cli.command("check-cases")
def jev_check_cases() -> None:
    """Run the labelled semantic boundary cases live (separately billed, ~30 requests) and report agreement."""
    from app.agent.jev_eval import run_eval

    report = run_eval()
    for q, v in report["by_question"].items():
        typer.echo(f"  {q:24s} {v['agree']}/{v['cases']}")
    for r in report["results"]:
        if not r["agree"]:
            typer.echo(f"  disagree: {r['case_id']}: expected {r['expected']!r}, got {r['answer']!r}")
    typer.echo(f"{report['agreement']}/{report['cases']} agree  ->  {report['path']}")


@cli.command()
def compare(run: str = typer.Option(..., help="Recorded run ID (runs/recorded/<id>)."),
            root: str = typer.Option("", help="Directory holding the run (default runs/recorded).")) -> None:
    """Scenarios, economics and recommendation for a recorded run on Slope's terms; writes scenarios.json and
    collections.csv into the run directory."""
    from datetime import date
    from pathlib import Path

    from app.agent.run_store import RunStore
    from app.config import ROOT
    from app.decisions.case import decide, export

    base = Path(root) if root else ROOT / "runs" / "recorded"
    store = RunStore(run, root=base)
    meta = store.events[0].payload
    inputs = meta["run_inputs"]
    from app.evidence.store import EvidenceStore

    review = date.fromisoformat(str(EvidenceStore(meta["snapshot_id"]).snapshot_info()["cutoff"])[:10])
    summary = decide(meta["snapshot_id"], inputs, list(store.graph["dispute_nodes"].values()), review)
    js, csv = export(summary, base / run)
    for view, rec in summary["recommendation"].items():
        typer.echo(f"{view:15s} -> {rec['structure']} (limit {rec['limit_cents'] / 100:,.0f}, "
                   f"order limit {rec['order_limit_cents'] / 100:,.0f})")
    for c in summary["conditions"]:
        typer.echo(f"  condition: {c['action']}")
    typer.echo(f"wrote {js.name} and {csv.name}")


@cli.command()
def investigate(
    snapshot: str = typer.Option("synergy_20240813", help="Dated evidence snapshot / case."),
    arm: str = typer.Option("agent_plus_jev", help="agent_plus_jev or agent_only."),
) -> None:
    """Run one recorded investigation (Claude subscription + separately billed Jev)."""
    import json as _json

    from app.agent.investigation import investigate as run

    record = run(snapshot, arm)
    keys = ("run_id", "status", "failure", "incomplete_reasons", "returned_models", "tool_calls", "graph_counts", "jev", "recorded_at")
    typer.echo(_json.dumps({k: record.get(k) for k in keys}, indent=2, default=str))
    raise typer.Exit(0 if record["status"] == "CANDIDATE_READY" else 1)


@cli.command()
def viewer(port: int = typer.Option(8000), host: str = typer.Option("127.0.0.1", help="Local only by default.")) -> None:
    """Serve the read-only investigation viewer for recorded runs."""
    import uvicorn

    uvicorn.run("app.web.app:app", host=host, port=port)


def main() -> None:
    cli()
