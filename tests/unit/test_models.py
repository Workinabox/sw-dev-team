from __future__ import annotations

import pytest
from pydantic import ValidationError

from wiab_team.models import (
    DeliveryKind,
    DeliveryReport,
    DevResult,
    ForgeKind,
    RepoRef,
    RunStatus,
    TaskSpec,
    TeamRunInput,
    TeamRunResult,
)


def _task() -> TaskSpec:
    return TaskSpec(title="Add rate limiting")


def test_local_delivery_needs_no_forge() -> None:
    payload = TeamRunInput(
        run_id="run-1",
        task=_task(),
        repo=RepoRef(remote="/tmp/repo.git"),
    )
    assert payload.delivery is DeliveryKind.LOCAL
    assert payload.repo.forge is ForgeKind.NONE


def test_push_without_a_forge_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires a forge"):
        TeamRunInput(
            run_id="run-1",
            task=_task(),
            repo=RepoRef(remote="/tmp/repo.git"),
            delivery=DeliveryKind.PUSH,
        )


def test_workinabox_forge_requires_its_identifiers() -> None:
    with pytest.raises(ValidationError, match="workinabox_repo_id"):
        RepoRef(remote="https://host/repos/R-3.git", forge=ForgeKind.WORKINABOX)


def test_github_forge_requires_its_identifier() -> None:
    with pytest.raises(ValidationError, match="github_repo"):
        RepoRef(remote="https://github.com/o/n.git", forge=ForgeKind.GITHUB)


def test_workinabox_repo_ref_round_trips() -> None:
    repo = RepoRef(
        remote="https://host/repos/R-3.git",
        forge=ForgeKind.WORKINABOX,
        workinabox_repo_id="R-3",
        workinabox_api_url="https://host",
    )
    assert RepoRef.model_validate(repo.model_dump()) == repo


def test_unknown_payload_fields_are_rejected() -> None:
    """Extra fields must fail loudly: the Rust side and this schema have to stay in step."""
    with pytest.raises(ValidationError):
        TeamRunInput.model_validate(
            {
                "run_id": "run-1",
                "task": {"title": "x"},
                "repo": {"remote": "/tmp/r.git"},
                "priority": "high",
            }
        )


def test_max_devs_is_bounded() -> None:
    with pytest.raises(ValidationError):
        TeamRunInput(run_id="run-1", task=_task(), repo=RepoRef(remote="/tmp/r.git"), max_devs=99)


def test_result_round_trips_through_json() -> None:
    result = TeamRunResult(
        run_id="run-1",
        status=RunStatus.PARTIAL,
        work_branch="wiab/run-1",
        dev_results=[DevResult(work_item_id="w1", branch="wiab/run-1/dev-1")],
        delivery=DeliveryReport(strategy="local", branch="wiab/run-1"),
    )
    assert TeamRunResult.model_validate_json(result.model_dump_json()) == result


def test_result_defaults_to_an_empty_but_valid_document() -> None:
    """A failed run must still produce a parseable result, not a half-written file."""
    result = TeamRunResult(run_id="run-1", status=RunStatus.FAILED, error="boom")
    payload = result.model_dump(mode="json")
    assert payload["schema_version"] == 1
    assert payload["dev_results"] == []
    assert payload["delivery"] is None
