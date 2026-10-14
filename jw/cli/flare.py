"""Lazy forwarding to the bounded forecasting toolkit CLI."""

import typer

from ._app import app


@app.command(
    "flare",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def flare(ctx: typer.Context):
    """Verify/import inputs, score a frozen model, or explicitly fetch/issue."""
    from jw.flare_forecast.cli import main

    raise typer.Exit(main(ctx.args or ["--help"]))
