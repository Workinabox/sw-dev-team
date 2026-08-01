"""The status endpoint, served for real and read over a real socket."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from wiab_team.worker.status import WorkerStatus, serve


@pytest.fixture
def endpoint() -> Iterator[tuple[WorkerStatus, str]]:
    status = WorkerStatus(team_id="TM-1", board_id="B-1")
    # Port 0: the OS picks a free one, so tests never collide with each other
    # or with whatever else is listening on this machine.
    server = serve(status, 0)
    try:
        yield status, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def test_health_answers_while_the_team_is_still_starting(
    endpoint: tuple[WorkerStatus, str],
) -> None:
    # Health must not depend on having claimed anything, or a team waiting on an
    # empty board would look broken.
    _, base = endpoint
    assert get(f"{base}/health") == {"status": "ok"}


def test_status_reports_what_the_team_is_working_on(
    endpoint: tuple[WorkerStatus, str],
) -> None:
    status, base = endpoint

    idle = get(f"{base}/status")
    assert idle["team_id"] == "TM-1"
    assert idle["board_id"] == "B-1"
    assert idle["state"] == "starting"
    assert idle["task_id"] is None
    assert idle["task_seconds"] is None

    status.working("T-7", "Add rate limiting")
    working = get(f"{base}/status")
    assert working["state"] == "working"
    assert working["task_id"] == "T-7"
    assert working["task_title"] == "Add rate limiting"
    # The number that answers "is it stuck?".
    assert working["task_seconds"] is not None


def test_a_completed_issue_is_counted_and_the_task_cleared(
    endpoint: tuple[WorkerStatus, str],
) -> None:
    status, base = endpoint
    status.working("T-1", "One")
    status.finished("completed")

    done = get(f"{base}/status")
    assert done["issues_completed"] == 1
    assert done["state"] == "waiting"
    assert done["task_id"] is None
    assert done["last_error"] is None


def test_a_failure_is_counted_as_no_issue_but_its_reason_is_kept(
    endpoint: tuple[WorkerStatus, str],
) -> None:
    # Whoever is curling a stuck team wants the last error more than a tally.
    status, base = endpoint
    status.working("T-1", "One")
    status.finished("failed", "tests never went green")

    failed = get(f"{base}/status")
    assert failed["issues_completed"] == 0
    assert failed["last_error"] == "tests never went green"


def test_a_paused_team_says_so(endpoint: tuple[WorkerStatus, str]) -> None:
    status, base = endpoint
    status.waiting(paused=True)
    assert get(f"{base}/status")["state"] == "paused"


def test_anything_else_is_a_404(endpoint: tuple[WorkerStatus, str]) -> None:
    _, base = endpoint
    with pytest.raises(urllib.error.HTTPError) as raised:
        get(f"{base}/shutdown")
    assert raised.value.code == 404
