"""The worker loop, driven against a fake board.

No server and no API key: the board is a fake, and ``run`` is substituted for
``run_team`` — the same arrangement ``ScriptedProvider`` gives the graph.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from wiab_team.config import Config, RoleConfig, ToolProviderKind
from wiab_team.errors import TeamError
from wiab_team.models.input import DeliveryKind, ForgeKind, TeamRunInput
from wiab_team.models.result import RunStatus, TeamRunResult
from wiab_team.worker.backend import BackendError, ClaimedTask
from wiab_team.worker.loop import WorkerSettings, build_input, work

pytestmark = pytest.mark.asyncio


def settings(**overrides: Any) -> WorkerSettings:
    base: dict[str, Any] = {
        "api_url": "https://wiab.example",
        "team_id": "TM-1",
        "board_id": "B-1",
        "repo_remote": "https://wiab.example/repos/R-7.git",
        "base_branch": "main",
        "forge": ForgeKind.WORKINABOX,
        "delivery": DeliveryKind.PR,
        "workinabox_repo_id": "R-7",
        "github_repo": None,
        # Near-zero: the tests exercise the loop, not the wait.
        "poll_interval_seconds": 0.001,
        "max_issues": 1,
    }
    return WorkerSettings(**{**base, **overrides})


def config() -> Config:
    return Config(
        api_key=None,
        tool_provider=ToolProviderKind.STUB,
        workspace=Path("/tmp/wiab-test"),
        roles={r: RoleConfig(model="m", effort=None) for r in ("architect", "dev", "tester")},
        dev_count=2,
        max_repair_rounds=1,
        max_conflict_rounds=1,
        checkpoint_dsn=None,
        log_level="INFO",
        log_json=False,
    )


def task(task_id: str = "T-1") -> ClaimedTask:
    return ClaimedTask(
        task_id=task_id,
        work_id="W-3",
        title="Ship it",
        description="the description",
        acceptance_criteria=["tests pass"],
    )


class FakeBoard:
    """Hands out a queued list of tasks, then reports the board empty, and
    records every outcome the loop reported back."""

    def __init__(self, tasks: list[ClaimedTask] | None = None) -> None:
        self._tasks = list(tasks or [])
        self.started: list[str] = []
        self.reported: list[tuple[str, str, str]] = []
        self.claims = 0
        self.claim_error: Exception | None = None
        self.state: str | None = "idle"
        self.holds: ClaimedTask | None = None

    async def team_state(self) -> str | None:
        return self.state

    async def held_task(self) -> ClaimedTask | None:
        return self.holds

    async def claim_next(self, board_id: str) -> ClaimedTask | None:
        self.claims += 1
        if self.claim_error is not None:
            error, self.claim_error = self.claim_error, None
            raise error
        return self._tasks.pop(0) if self._tasks else None

    async def start(self, task_id: str) -> None:
        self.started.append(task_id)

    async def complete(self, task_id: str) -> None:
        self.reported.append((task_id, "complete", ""))

    async def fail(self, task_id: str, reason: str) -> None:
        self.reported.append((task_id, "fail", reason))

    async def escalate(self, task_id: str, reason: str) -> None:
        self.reported.append((task_id, "escalate", reason))


def result(status: RunStatus, **overrides: Any) -> TeamRunResult:
    return TeamRunResult(run_id="T-1", status=status, **overrides)


def returns(value: TeamRunResult) -> Any:
    async def run(payload: TeamRunInput, **_: Any) -> TeamRunResult:
        return value

    return run


async def test_build_input_carries_the_task_onto_the_payload() -> None:
    payload = build_input(task(), settings(), max_devs=3)
    # The run id is the task id so a resumed run lands on the same checkpoint.
    assert payload.run_id == "T-1"
    assert payload.task.external_id == "W-3"
    assert payload.task.title == "Ship it"
    assert payload.task.acceptance_criteria == ["tests pass"]
    assert payload.repo.remote == "https://wiab.example/repos/R-7.git"
    assert payload.repo.workinabox_api_url == "https://wiab.example"
    assert payload.max_devs == 3


async def test_a_github_payload_carries_no_workinabox_api_url() -> None:
    payload = build_input(
        task(),
        settings(
            forge=ForgeKind.GITHUB,
            workinabox_repo_id=None,
            github_repo="acme/widgets",
            repo_remote="https://github.com/acme/widgets.git",
        ),
        max_devs=1,
    )
    assert payload.repo.workinabox_api_url is None
    assert payload.repo.github_repo == "acme/widgets"


async def test_a_succeeded_run_completes_the_task() -> None:
    board = FakeBoard([task()])
    ran = await work(
        board,
        settings(),
        config(),
        run=returns(result(RunStatus.SUCCEEDED, work_branch="wiab/T-1")),
    )
    assert ran == 1
    assert board.started == ["T-1"]
    assert board.reported == [("T-1", "complete", "")]


async def test_a_partial_run_is_escalated_not_failed() -> None:
    # Partial means real work landed; escalating puts it back on the board with
    # context instead of burying it.
    board = FakeBoard([task()])
    await work(
        board,
        settings(),
        config(),
        run=returns(result(RunStatus.PARTIAL, work_branch="wiab/T-1")),
    )
    task_id, outcome, reason = board.reported[0]
    assert (task_id, outcome) == ("T-1", "escalate")
    assert "wiab/T-1" in reason


async def test_a_failed_run_fails_the_task_with_its_error() -> None:
    board = FakeBoard([task()])
    await work(
        board,
        settings(),
        config(),
        run=returns(result(RunStatus.FAILED, error="tests never went green")),
    )
    assert board.reported == [("T-1", "fail", "tests never went green")]


async def test_a_crash_fails_the_task_rather_than_leaving_it_in_progress() -> None:
    # A task stuck in_progress is invisible: no team can take it and nobody is told.
    async def explode(payload: TeamRunInput, **_: Any) -> TeamRunResult:
        raise RuntimeError("boom")

    board = FakeBoard([task()])
    await work(board, settings(), config(), run=explode)
    task_id, outcome, reason = board.reported[0]
    assert (task_id, outcome) == ("T-1", "fail")
    assert "RuntimeError: boom" in reason


async def test_a_run_that_cannot_start_fails_the_task() -> None:
    async def refuse(payload: TeamRunInput, **_: Any) -> TeamRunResult:
        raise TeamError("no git remote")

    board = FakeBoard([task()])
    await work(board, settings(), config(), run=refuse)
    task_id, outcome, reason = board.reported[0]
    assert (task_id, outcome) == ("T-1", "fail")
    assert "no git remote" in reason


async def test_the_task_is_not_worked_on_if_it_cannot_be_marked_started() -> None:
    # Otherwise the board's record would disagree with what the team is doing.
    ran: list[str] = []

    async def record(payload: TeamRunInput, **_: Any) -> TeamRunResult:
        ran.append(payload.run_id)
        return result(RunStatus.SUCCEEDED)

    board = FakeBoard([task()])

    async def refuse(task_id: str) -> None:
        raise BackendError("503")

    board.start = refuse  # type: ignore[method-assign]
    await work(board, settings(), config(), run=record)
    assert ran == []
    assert board.reported == []


async def test_a_lost_report_does_not_take_the_team_down() -> None:
    board = FakeBoard([task()])

    async def refuse(task_id: str) -> None:
        raise BackendError("503")

    board.complete = refuse  # type: ignore[method-assign]
    ran = await work(board, settings(), config(), run=returns(result(RunStatus.SUCCEEDED)))
    assert ran == 1


async def test_an_unreachable_backend_is_retried_not_fatal() -> None:
    board = FakeBoard([task()])
    board.claim_error = BackendError("connection refused")
    ran = await work(board, settings(), config(), run=returns(result(RunStatus.SUCCEEDED)))
    # The first claim raised; the loop waited and asked again.
    assert board.claims == 2
    assert ran == 1


async def test_the_loop_keeps_pulling_until_its_issue_limit() -> None:
    board = FakeBoard([task("T-1"), task("T-2")])
    ran = await work(
        board, settings(max_issues=2), config(), run=returns(result(RunStatus.SUCCEEDED))
    )
    assert ran == 2
    assert board.started == ["T-1", "T-2"]


async def test_an_empty_board_is_waited_out_and_a_stop_ends_the_wait() -> None:
    board = FakeBoard([])
    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.01)
        stop.set()

    _, ran = await asyncio.gather(
        stop_soon(),
        work(
            board,
            settings(max_issues=0),
            config(),
            stop=stop,
            run=returns(result(RunStatus.SUCCEEDED)),
        ),
    )
    assert ran == 0
    assert board.claims >= 1


async def test_a_stop_set_before_the_first_claim_runs_nothing() -> None:
    board = FakeBoard([task()])
    stop = asyncio.Event()
    stop.set()
    ran = await work(
        board, settings(), config(), stop=stop, run=returns(result(RunStatus.SUCCEEDED))
    )
    assert ran == 0
    assert board.claims == 0


async def test_a_paused_team_takes_no_new_work() -> None:
    board = FakeBoard([task()])
    board.state = "paused"
    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.01)
        stop.set()

    _, ran = await asyncio.gather(
        stop_soon(),
        work(
            board,
            settings(max_issues=0),
            config(),
            stop=stop,
            run=returns(result(RunStatus.SUCCEEDED)),
        ),
    )
    assert ran == 0
    assert board.claims == 0, "a paused team must not even ask the board"


async def test_a_paused_run_is_not_reported_and_is_picked_up_again() -> None:
    # The team still holds the task, so reporting anything would be a lie — and
    # escalating it would hand half-finished work to someone else.
    board = FakeBoard([task()])
    states = iter(["idle", "idle", "idle"])

    async def next_state() -> str | None:
        return next(states, "idle")

    board.team_state = next_state  # type: ignore[method-assign]
    resumes: list[bool] = []

    async def run(payload: TeamRunInput, **kwargs: Any) -> TeamRunResult:
        resumes.append(bool(kwargs.get("resume")))
        # Pause the first time, finish the second.
        if len(resumes) == 1:
            return result(RunStatus.PAUSED)
        return result(RunStatus.SUCCEEDED)

    ran = await work(board, settings(max_issues=1), config(), run=run)

    assert resumes == [False, True], "the second attempt resumed rather than restarted"
    assert board.started == ["T-1"], "a resumed task is not started twice"
    assert board.reported == [("T-1", "complete", "")]
    assert ran == 1


async def test_a_team_restarting_picks_up_the_task_it_already_holds() -> None:
    # Otherwise the old task sits in progress forever with nobody working on it.
    board = FakeBoard([task("T-9")])
    board.holds = task("T-1")
    ran = await work(
        board, settings(max_issues=1), config(), run=returns(result(RunStatus.SUCCEEDED))
    )
    assert ran == 1
    assert board.reported == [("T-1", "complete", "")]
    assert board.claims == 0, "held work comes before anything new"
    assert board.started == [], "a held task is already in progress"


async def test_an_unreachable_backend_does_not_look_like_a_pause() -> None:
    # Treating "cannot reach" as "paused" would idle a team that should be working.
    board = FakeBoard([task()])

    async def refuse() -> str | None:
        raise BackendError("503")

    board.team_state = refuse  # type: ignore[method-assign]
    ran = await work(
        board, settings(max_issues=1), config(), run=returns(result(RunStatus.SUCCEEDED))
    )
    assert ran == 1
    assert board.reported == [("T-1", "complete", "")]


async def test_a_pause_during_a_run_reaches_the_graph() -> None:
    # The point of watching: a pause takes effect at the next node boundary rather
    # than waiting for the whole issue to finish.
    board = FakeBoard([task()])
    states = iter(["idle"])
    stop = asyncio.Event()

    async def next_state() -> str | None:
        # Idle for the loop's own check, then paused for the watcher.
        return next(states, "paused")

    board.team_state = next_state  # type: ignore[method-assign]
    saw_pause = asyncio.Event()

    async def run(payload: TeamRunInput, **kwargs: Any) -> TeamRunResult:
        pause = kwargs["pause"]
        assert pause is not None, "a checkpointed run must be pausable"
        await asyncio.wait_for(pause.wait(), timeout=1)
        saw_pause.set()
        # A paused team then waits for a resume that never comes here, so end the loop.
        stop.set()
        return result(RunStatus.PAUSED)

    await asyncio.wait_for(
        work(board, settings(max_issues=0), config(), stop=stop, checkpointer=object(), run=run),
        timeout=5,
    )
    assert saw_pause.is_set()
    assert board.reported == [], "a paused task is not reported"


async def test_a_run_without_a_checkpointer_is_not_given_a_pause_event() -> None:
    # run_team refuses a pause it cannot checkpoint, so there is no point offering one.
    board = FakeBoard([task()])
    seen: list[object] = []

    async def run(payload: TeamRunInput, **kwargs: Any) -> TeamRunResult:
        seen.append(kwargs.get("pause"))
        return result(RunStatus.SUCCEEDED)

    await work(board, settings(max_issues=1), config(), run=run)
    assert seen == [None]


async def test_a_backend_that_cannot_say_what_is_held_does_not_stop_the_team() -> None:
    board = FakeBoard([task()])

    async def refuse() -> ClaimedTask | None:
        raise BackendError("503")

    board.held_task = refuse  # type: ignore[method-assign]
    ran = await work(
        board, settings(max_issues=1), config(), run=returns(result(RunStatus.SUCCEEDED))
    )
    assert ran == 1
