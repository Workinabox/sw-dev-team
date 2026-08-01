"""The board client against a faked HTTP backend.

These assertions mirror the routes and payloads the Rust backend actually
implements (`backend/crates/wiab-inf/src/http_api.rs`, task module, and
`wiab-core`'s `TaskSnapshot` / `WorkSnapshot`). If the two drift, this test is
where it shows up.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from wiab_team.worker.backend import BackendClient, BackendError

API = "https://wiab.example.com"


def client() -> BackendClient:
    return BackendClient(api_url=API, team_id="TM-1", token="tok")


def task_snapshot(**overrides: object) -> dict[str, object]:
    return {
        "id": "T-4",
        "board_id": "B-1",
        "work_id": "W-3",
        "state": "assigned",
        "assignee": "TM-1",
        "reason": None,
        **overrides,
    }


def work_snapshot(dones: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "id": "W-3",
        "project_id": "P-1",
        "title": "Add rate limiting",
        "description": "the description",
        "dones": dones if dones is not None else [],
        "is_done": False,
    }


@respx.mock
async def test_a_claim_joins_the_task_to_its_work() -> None:
    claim = respx.post(f"{API}/boards/B-1/tasks/claim").mock(
        return_value=httpx.Response(200, json=task_snapshot())
    )
    respx.get(f"{API}/works/W-3").mock(
        return_value=httpx.Response(
            200,
            json=work_snapshot(
                [
                    {"id": "D-1", "criterion": "tests pass", "fulfilled": False},
                    {"id": "D-2", "criterion": "docs updated", "fulfilled": False},
                ]
            ),
        )
    )

    task = await client().claim_next("B-1")

    assert task is not None
    assert task.task_id == "T-4"
    assert task.work_id == "W-3"
    assert task.title == "Add rate limiting"
    assert task.description == "the description"
    assert task.acceptance_criteria == ["tests pass", "docs updated"]
    # The team claims as itself, which is how the backend records the assignee.
    assert json.loads(claim.calls[0].request.read()) == {"team_id": "TM-1"}


@respx.mock
async def test_a_fulfilled_criterion_is_not_restated_as_a_requirement() -> None:
    respx.post(f"{API}/boards/B-1/tasks/claim").mock(
        return_value=httpx.Response(200, json=task_snapshot())
    )
    respx.get(f"{API}/works/W-3").mock(
        return_value=httpx.Response(
            200,
            json=work_snapshot(
                [
                    {"id": "D-1", "criterion": "already done", "fulfilled": True},
                    {"id": "D-2", "criterion": "still outstanding", "fulfilled": False},
                ]
            ),
        )
    )

    task = await client().claim_next("B-1")

    assert task is not None
    assert task.acceptance_criteria == ["still outstanding"]


@respx.mock
async def test_an_empty_board_is_none_not_an_error() -> None:
    # The backend answers 404 for "nothing waiting"; a polling team just waits.
    respx.post(f"{API}/boards/B-1/tasks/claim").mock(return_value=httpx.Response(404))
    assert await client().claim_next("B-1") is None


@respx.mock
async def test_a_rejected_claim_is_an_error() -> None:
    respx.post(f"{API}/boards/B-1/tasks/claim").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    with pytest.raises(BackendError, match="403"):
        await client().claim_next("B-1")


@respx.mock
async def test_an_unreachable_backend_is_an_error_naming_the_url() -> None:
    respx.post(f"{API}/boards/B-1/tasks/claim").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(BackendError, match="boards/B-1/tasks/claim"):
        await client().claim_next("B-1")


@respx.mock
async def test_the_outcome_routes_carry_the_reason_the_backend_expects() -> None:
    start = respx.post(f"{API}/tasks/T-4/start").mock(
        return_value=httpx.Response(200, json=task_snapshot(state="in_progress"))
    )
    complete = respx.post(f"{API}/tasks/T-4/complete").mock(
        return_value=httpx.Response(200, json=task_snapshot(state="completed"))
    )
    fail = respx.post(f"{API}/tasks/T-4/fail").mock(
        return_value=httpx.Response(200, json=task_snapshot(state="failed"))
    )
    escalate = respx.post(f"{API}/tasks/T-4/escalate").mock(
        return_value=httpx.Response(200, json=task_snapshot(state="escalated"))
    )

    api = client()
    await api.start("T-4")
    await api.complete("T-4")
    await api.fail("T-4", "tests never went green")
    await api.escalate("T-4", "needs a decision")

    assert start.called and complete.called
    assert json.loads(fail.calls[0].request.read()) == {"reason": "tests never went green"}
    assert json.loads(escalate.calls[0].request.read()) == {"reason": "needs a decision"}


@respx.mock
async def test_closing_twice_is_safe() -> None:
    api = client()
    respx.post(f"{API}/tasks/T-4/start").mock(
        return_value=httpx.Response(200, json=task_snapshot())
    )
    await api.start("T-4")
    await api.aclose()
    await api.aclose()
