"""The worker loop: claim a task, run the team on it, report the outcome, repeat.

This is what makes a team *long-lived*. The graph itself is unchanged — one
pass through ``run_team`` is still one issue — but the process around it no
longer exits when that pass finishes. It goes back to the board.

Stopping is cooperative. A stop signal is honoured between issues, never
during one: killing a team mid-run would leave a half-written worktree and a
task nobody has reported on. The cost is that a stop can wait as long as one
issue takes; the benefit is that the board is always consistent.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Protocol

from wiab_team.config import Config
from wiab_team.errors import TeamError
from wiab_team.logging import get_logger
from wiab_team.models.input import DeliveryKind, ForgeKind, RepoRef, TaskSpec, TeamRunInput
from wiab_team.models.result import RunStatus, TeamRunResult
from wiab_team.worker.backend import BackendError, ClaimedTask

log = get_logger(__name__)


# The backend's word for "stop taking work, and stop what you are doing".
PAUSED = "paused"


class Board(Protocol):
    """What the loop needs from the backend. A protocol so tests can supply a
    fake without a server, as ``ToolProvider`` has ``ScriptedProvider``."""

    async def team_state(self) -> str | None: ...
    async def held_task(self) -> ClaimedTask | None: ...
    async def claim_next(self, board_id: str) -> ClaimedTask | None: ...
    async def start(self, task_id: str) -> None: ...
    async def complete(self, task_id: str) -> None: ...
    async def fail(self, task_id: str, reason: str) -> None: ...
    async def escalate(self, task_id: str, reason: str) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """Which board this team pulls from, and where the code lives."""

    api_url: str
    team_id: str
    board_id: str
    repo_remote: str
    base_branch: str
    forge: ForgeKind
    delivery: DeliveryKind
    workinabox_repo_id: str | None
    github_repo: str | None
    # How long to wait after finding the board empty.
    poll_interval_seconds: float
    # Stop after this many issues. 0 means keep going; used by tests and by a
    # one-shot run.
    max_issues: int = 0


def build_input(task: ClaimedTask, settings: WorkerSettings, *, max_devs: int) -> TeamRunInput:
    """Turn a claimed task into the payload the graph already understands.

    The run id is the task id: it is unique, it is what the backend calls this
    piece of work, and it is what LangGraph checkpoints are keyed on — so a
    resumed run lands on the same thread.
    """
    return TeamRunInput(
        run_id=task.task_id,
        task=TaskSpec(
            external_id=task.work_id,
            title=task.title,
            description=task.description,
            acceptance_criteria=task.acceptance_criteria,
        ),
        repo=RepoRef(
            remote=settings.repo_remote,
            base_branch=settings.base_branch,
            forge=settings.forge,
            workinabox_repo_id=settings.workinabox_repo_id,
            workinabox_api_url=settings.api_url if settings.forge is ForgeKind.WORKINABOX else None,
            github_repo=settings.github_repo,
        ),
        delivery=settings.delivery,
        max_devs=max_devs,
    )


async def work(
    board: Board,
    settings: WorkerSettings,
    config: Config,
    *,
    stop: asyncio.Event | None = None,
    checkpointer: Any | None = None,
    run: Any | None = None,
) -> int:
    """Pull issues until asked to stop. Returns how many were run.

    ``run`` exists so tests can substitute ``run_team``; nothing else overrides it.
    """
    if run is None:
        from wiab_team.api import run_team

        run = run_team

    stop = stop or asyncio.Event()
    completed = 0
    log.info(
        "worker_started",
        team_id=settings.team_id,
        board_id=settings.board_id,
        poll_interval=settings.poll_interval_seconds,
    )

    # A team that was stopped mid-issue still owns its task, so it resumes that before
    # taking anything new — otherwise the old task sits in progress with nobody on it.
    resuming = await _held(board)

    while not stop.is_set():
        try:
            paused = await board.team_state() == PAUSED
        except BackendError as exc:
            # Unreachable is not the same as paused. Keep going rather than idling on a
            # guess; the claim below will fail too and back off.
            log.warning("team_state_failed", error=str(exc))
            paused = False

        if paused:
            log.debug("team_paused", team_id=settings.team_id)
            await _sleep_or_stop(settings.poll_interval_seconds, stop)
            continue

        task, resume = (resuming, True) if resuming else (None, False)
        resuming = None
        if task is None:
            try:
                task = await board.claim_next(settings.board_id)
            except BackendError as exc:
                # The backend being briefly unreachable is not a reason to tear the
                # team down; it is a reason to wait and ask again.
                log.warning("claim_failed", error=str(exc))
                await _sleep_or_stop(settings.poll_interval_seconds, stop)
                continue

        if task is None:
            log.debug("board_empty", board_id=settings.board_id)
            await _sleep_or_stop(settings.poll_interval_seconds, stop)
            continue

        paused_run = await _run_one(
            board, task, settings, config, checkpointer=checkpointer, run=run, resume=resume
        )
        if paused_run:
            # The team still holds the task and the checkpoint holds the run. Pick it up
            # again when the pause lifts rather than claiming something new.
            resuming = task
            continue
        completed += 1
        if settings.max_issues and completed >= settings.max_issues:
            log.info("worker_reached_issue_limit", max_issues=settings.max_issues)
            break

    log.info("worker_stopped", issues=completed)
    return completed


async def _run_one(
    board: Board,
    task: ClaimedTask,
    settings: WorkerSettings,
    config: Config,
    *,
    checkpointer: Any | None,
    run: Any,
    resume: bool = False,
) -> bool:
    """Run one issue and report it, whatever happens.

    Returns whether the run stopped because a pause was requested — the one outcome that
    is not reported, because the team still holds the task and means to finish it.

    Every other exit from this function reports *something* to the backend. A task left
    `in_progress` because the team crashed is invisible: no other team can take it and no
    human is told it stalled.
    """
    log.info(
        "task_claimed",
        task_id=task.task_id,
        work_id=task.work_id,
        title=task.title,
        resumed=resume,
    )
    if not resume:
        # Already in progress when resuming; starting it again would be rejected.
        try:
            await board.start(task.task_id)
        except BackendError as exc:
            # We hold the task but cannot mark it started, so we must not work on
            # it — the board's record would disagree with reality.
            log.error("task_start_failed", task_id=task.task_id, error=str(exc))
            return False

    pause = asyncio.Event()
    watcher = asyncio.create_task(_watch_for_pause(board, settings, pause))
    try:
        payload = build_input(task, settings, max_devs=config.dev_count)
        result: TeamRunResult = await run(
            payload,
            config=config,
            checkpointer=checkpointer,
            pause=pause if checkpointer is not None else None,
            resume=resume,
        )
    except TeamError as exc:
        await _report(board.fail, task.task_id, f"the run could not start: {exc}")
        return False
    except Exception as exc:  # the loop must survive any one issue
        log.exception("run_crashed", task_id=task.task_id)
        await _report(board.fail, task.task_id, f"{type(exc).__name__}: {exc}")
        return False
    finally:
        watcher.cancel()

    if result.status is RunStatus.PAUSED:
        # Deliberately unreported: the task stays in progress and this team keeps it.
        log.info("task_paused", task_id=task.task_id)
        return True

    if result.status is RunStatus.SUCCEEDED:
        await _report(board.complete, task.task_id)
    elif result.status is RunStatus.PARTIAL:
        # Partial means real work landed but not all of it. Escalating puts it
        # back on the board with context rather than burying it as failed.
        await _report(
            board.escalate,
            task.task_id,
            result.error or f"partial run; work is on {result.work_branch or '(no branch)'}",
        )
    else:
        await _report(board.fail, task.task_id, result.error or "the run failed")

    log.info("task_reported", task_id=task.task_id, status=result.status.value)
    return False


async def _held(board: Board) -> ClaimedTask | None:
    """The task this team already owns, if the backend can be reached to say so."""
    try:
        task = await board.held_task()
    except BackendError as exc:
        log.warning("held_task_failed", error=str(exc))
        return None
    if task is not None:
        log.info("resuming_held_task", task_id=task.task_id)
    return task


async def _watch_for_pause(board: Board, settings: WorkerSettings, pause: asyncio.Event) -> None:
    """Set `pause` as soon as the backend says the team is paused.

    Runs alongside the graph so a pause takes effect at the next node boundary rather than
    waiting for the whole issue to finish.
    """
    while not pause.is_set():
        await asyncio.sleep(settings.poll_interval_seconds)
        try:
            if await board.team_state() == PAUSED:
                log.info("pause_requested", team_id=settings.team_id)
                pause.set()
        except BackendError as exc:
            # A pause we could not hear about is not a reason to stop working.
            log.warning("pause_poll_failed", error=str(exc))


async def _report(report: Any, task_id: str, reason: str | None = None) -> None:
    """Report an outcome, and log rather than raise if that call itself fails —
    a lost report must not take the whole team down with it."""
    try:
        if reason is None:
            await report(task_id)
        else:
            await report(task_id, reason)
    except BackendError as exc:
        log.error("task_report_failed", task_id=task_id, error=str(exc))


async def _sleep_or_stop(seconds: float, stop: asyncio.Event) -> None:
    """Wait out the poll interval, but wake immediately on a stop signal, so a
    stop is not held up by an idle wait."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
