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
from wiab_team.config import load_worker as load_worker_config
from wiab_team.errors import ConfigError, TeamError
from wiab_team.models.input import TeamRunInput
from wiab_team.models.result import RunStatus, TeamRunResult

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Run an agent dev team.")

# Exit codes the backend can branch on without parsing anything.
EXIT_OK = 0
EXIT_PARTIAL = 2
EXIT_FAILED = 1
EXIT_MISCONFIGURED = 3
EXIT_PAUSED = 4


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
            # A paused run is unfinished, not failed; the caller resumes it.
            RunStatus.PAUSED: EXIT_PAUSED,
        }[result.status]
    )


@app.command()
def work(
    max_issues: Annotated[
        int,
        typer.Option("--max-issues", help="Stop after this many issues. 0 keeps going."),
    ] = 0,
) -> None:
    """Pull issues from the team's board and run them, until stopped.

    This is the long-lived mode: unlike `run`, it does not exit when an issue
    finishes — it goes back to the board.
    """
    try:
        config = load_config()
        worker_config = load_worker_config()
    except ConfigError as exc:
        typer.secho(f"configuration error: {exc}", fg="red", err=True)
        raise typer.Exit(EXIT_MISCONFIGURED) from exc

    team_logging.configure(level=config.log_level, json_output=config.log_json)
    _trust_backend_certificate(config.api_certificate_pem, config.workspace)

    from wiab_team.models.input import DeliveryKind, ForgeKind
    from wiab_team.worker import BackendClient, WorkerSettings
    from wiab_team.worker import work as run_worker

    settings = WorkerSettings(
        api_url=worker_config.api_url,
        team_id=worker_config.team_id,
        board_id=worker_config.board_id,
        repo_remote=worker_config.repo_remote,
        base_branch=worker_config.base_branch,
        # A team pulling from a workinabox board is working on a workinabox
        # repo, so it delivers a pull request there. Nothing else would make
        # sense for work the backend handed out.
        forge=ForgeKind.WORKINABOX,
        delivery=DeliveryKind.PR,
        workinabox_repo_id=_repo_id_from(worker_config.repo_remote),
        github_repo=None,
        poll_interval_seconds=worker_config.poll_interval_seconds,
        max_issues=max_issues,
    )

    async def go() -> int:
        client = BackendClient(
            api_url=worker_config.api_url,
            team_id=worker_config.team_id,
            token=worker_config.token,
            certificate_pem=config.api_certificate_pem,
        )
        stop = asyncio.Event()
        _install_stop_handlers(stop)
        try:
            if not config.checkpoint_dsn:
                return await run_worker(client, settings, config, stop=stop)

            from wiab_team.checkpoint import checkpointer

            async with checkpointer(config.checkpoint_dsn) as saver:
                return await run_worker(client, settings, config, stop=stop, checkpointer=saver)
        finally:
            await client.aclose()

    try:
        issues = asyncio.run(go())
    except TeamError as exc:
        typer.secho(f"worker could not start: {exc}", fg="red", err=True)
        raise typer.Exit(EXIT_MISCONFIGURED) from exc

    print(f"\nstopped after {issues} issue(s)", file=sys.stderr)


def _trust_backend_certificate(certificate_pem: str | None, workspace: Path) -> None:
    """Let git verify the backend too.

    The HTTP client is handed the certificate directly, but cloning and pushing go through
    git, which reads it from a file. Writing it once here covers every git call in the
    process without threading a path through the worktree machinery.
    """
    if not certificate_pem:
        return
    import os

    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "backend-ca.pem"
    path.write_text(certificate_pem, encoding="utf-8")
    os.environ["GIT_SSL_CAINFO"] = str(path)


def _repo_id_from(remote: str) -> str | None:
    """Recover the `R-<n>` id from a workinabox clone URL.

    The backend serves repos at `<host>/repos/R-<n>.git`, and pull requests are
    keyed on that id — so the remote already carries it and there is no second
    setting to keep in step with the first.
    """
    tail = remote.rstrip("/").rsplit("/", 1)[-1]
    name = tail.removesuffix(".git")
    return name if name.startswith("R-") else None


def _install_stop_handlers(stop: asyncio.Event) -> None:
    """Stop on SIGTERM/SIGINT. The loop finishes the issue in hand first, so a
    `docker stop` never severs a run mid-way."""
    import contextlib
    import signal

    running = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        # Not every platform can install these; a team that cannot catch a
        # signal still works, it just stops abruptly.
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - POSIX only
            running.add_signal_handler(signal_number, stop.set)


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
        RunStatus.PAUSED: "cyan",
    }[result.status]
    print(f"\n{result.status.value}: {result.run_id}", file=sys.stderr)
    typer.secho(f"  branch: {result.work_branch or '(none)'}", fg=colour, err=True)
    if result.error:
        typer.secho(f"  error:  {result.error}", fg="red", err=True)
    if result.delivery and result.delivery.pull_request_url:
        typer.secho(f"  pr:     {result.delivery.pull_request_url}", fg=colour, err=True)


if __name__ == "__main__":  # pragma: no cover
    app()
