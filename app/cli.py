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


def main() -> None:
    cli()
