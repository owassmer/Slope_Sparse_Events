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


def main() -> None:
    cli()
