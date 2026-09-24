"""Command-line entry point. Subcommands are added by each build step."""

import typer

cli = typer.Typer(no_args_is_help=True, help="Slope external-event credit scenario module")


@cli.command()
def version() -> None:
    """Print the package version."""
    from importlib.metadata import version as _v

    typer.echo(_v("slope-sparse-events"))


def main() -> None:
    cli()
