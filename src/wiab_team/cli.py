"""Thin command-line wrapper over :func:`wiab_team.api.run_team`.

Deliberately thin: the invocation protocol between the Rust backend and this
package is not settled yet, so nothing that matters lives here. Everything the
CLI does, a future server would do the same way through ``api.run_team``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from wiab_team import logging as team_logging
from wiab_team.config import ToolProviderKind
from wiab_team.config import load as load_config
from wiab_team.errors import ConfigError, TeamError
from wiab_team.models.input import TeamRunInput
from wiab_team.models.result import RunStatus, TeamRunResult

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Run an agent dev team.")

# Exit codes the backend can branch on without parsing anything.
EXIT_OK = 0
EXIT_PARTIAL = 2
EXIT_FAILED = 1
EXIT_MISCONFIGURED = 3


@app.command()
def run(
    input_file: Annotated[
        Path, typer.Option("--input-file", "-i", help="Path to the JSON payload.")
    ],
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Override WIAB_TEAM_TOOL_PROVIDER (claude_sdk|stub)."),
    ] = None,
    workspace: Annotated[
        Path | None, typer.Option("--workspace", help="Override WIAB_TEAM_WORKSPACE.")
    ] = None,
) -> None:
    """Run a team against a payload and write result.json + report.md."""
    import os

    if provider:
        os.environ["WIAB_TEAM_TOOL_PROVIDER"] = provider

    try:
        config = load_config()
    except ConfigError as exc:
        typer.secho(f"configuration error: {exc}", fg="red", err=True)
        raise typer.Exit(EXIT_MISCONFIGURED) from exc

    team_logging.configure(level=config.log_level, json_output=config.log_json)

    try:
        payload = TeamRunInput.model_validate_json(input_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.secho(f"invalid payload: {exc}", fg="red", err=True)
        raise typer.Exit(EXIT_MISCONFIGURED) from exc

    from wiab_team.api import run_team

    async def go() -> TeamRunResult:
        if not config.checkpoint_dsn:
            return await run_team(payload, config=config, workspace=workspace)

        from wiab_team.checkpoint import checkpointer

        async with checkpointer(config.checkpoint_dsn) as saver:
            return await run_team(payload, config=config, workspace=workspace, checkpointer=saver)

    try:
        result = asyncio.run(go())
    except TeamError as exc:
        typer.secho(f"run could not start: {exc}", fg="red", err=True)
        raise typer.Exit(EXIT_MISCONFIGURED) from exc

    _summarize(result)
    raise typer.Exit(
        {
            RunStatus.SUCCEEDED: EXIT_OK,
            RunStatus.PARTIAL: EXIT_PARTIAL,
            RunStatus.FAILED: EXIT_FAILED,
        }[result.status]
    )


@app.command("validate-config")
def validate_config() -> None:
    """Check the environment without running anything."""
    try:
        config = load_config()
    except ConfigError as exc:
        typer.secho(f"configuration error: {exc}", fg="red", err=True)
        raise typer.Exit(EXIT_MISCONFIGURED) from exc

    typer.echo(f"provider:  {config.tool_provider.value}")
    typer.echo(f"workspace: {config.workspace}")
    for role in ("architect", "dev", "tester"):
        settings = config.role(role)
        typer.echo(f"{role:<10} {settings.model} (effort: {settings.effort or 'default'})")
    if config.tool_provider is ToolProviderKind.CLAUDE_SDK:
        typer.echo("api key:   present")


@app.command()
def schema(
    which: Annotated[str, typer.Argument(help="'input' or 'result'.")],
) -> None:
    """Print a JSON Schema, so the Rust side can generate types from it."""
    models: dict[str, type[BaseModel]] = {"input": TeamRunInput, "result": TeamRunResult}
    model = models.get(which)
    if model is None:
        typer.secho(
            f"unknown schema {which!r}; expected one of {sorted(models)}", fg="red", err=True
        )
        raise typer.Exit(EXIT_MISCONFIGURED)
    typer.echo(json.dumps(model.model_json_schema(), indent=2))


def _summarize(result: TeamRunResult) -> None:
    """A short human summary on stderr; stdout stays reserved for the log stream."""
    colour = {
        RunStatus.SUCCEEDED: "green",
        RunStatus.PARTIAL: "yellow",
        RunStatus.FAILED: "red",
    }[result.status]
    print(f"\n{result.status.value}: {result.run_id}", file=sys.stderr)
    typer.secho(f"  branch: {result.work_branch or '(none)'}", fg=colour, err=True)
    if result.error:
        typer.secho(f"  error:  {result.error}", fg="red", err=True)
    if result.delivery and result.delivery.pull_request_url:
        typer.secho(f"  pr:     {result.delivery.pull_request_url}", fg=colour, err=True)


if __name__ == "__main__":  # pragma: no cover
    app()
