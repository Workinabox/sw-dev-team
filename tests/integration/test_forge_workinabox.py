"""The workinabox forge against a faked HTTP backend.

These assertions mirror the routes and payload the Rust backend actually
implements (`backend/crates/wiab-inf/src/http_api.rs`, `pull_request` module).
If the two drift, this test is where it shows up.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from wiab_team.errors import ForgeError
from wiab_team.forge.workinabox import WorkinaboxForge

API = "https://wiab.example.com"


def forge() -> WorkinaboxForge:
    return WorkinaboxForge(api_url=API, repo_id="R-3", token="tok")


@respx.mock
async def test_opens_a_pull_request_with_the_backends_field_names() -> None:
    route = respx.post(f"{API}/repos/R-3/pull-requests").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "PR-7",
                "repo_id": "R-3",
                "author_id": "U-1",
                "title": "Add rate limiting",
                "description": "body",
                "source_branch": "wiab/run-1",
                "target_branch": "main",
                "state": "open",
                "merge_commit": None,
                "opened_at": "2026-07-31T12:00:00Z",
            },
        )
    )

    pull_request = await forge().open_pull_request(
        title="Add rate limiting",
        body="body",
        source_branch="wiab/run-1",
        target_branch="main",
    )

    assert pull_request.id == "PR-7"
    assert pull_request.url == f"{API}/pull-requests/PR-7"

    request = route.calls.last.request
    # The backend deserialises OpenPullRequestRequest, which names the body field
    # `description` — not `body`.
    assert request.headers["Authorization"] == "Bearer tok"
    import json

    assert json.loads(request.content) == {
        "title": "Add rate limiting",
        "description": "body",
        "source_branch": "wiab/run-1",
        "target_branch": "main",
    }


@respx.mock
async def test_a_404_explains_itself() -> None:
    respx.post(f"{API}/repos/R-3/pull-requests").mock(return_value=httpx.Response(404))
    with pytest.raises(ForgeError, match="404"):
        await forge().open_pull_request(
            title="t", body="b", source_branch="feature", target_branch="main"
        )


@respx.mock
async def test_a_rejection_surfaces_the_backends_message() -> None:
    respx.post(f"{API}/repos/R-3/pull-requests").mock(
        return_value=httpx.Response(400, text="branch 'nope' does not exist")
    )
    with pytest.raises(ForgeError, match="nope"):
        await forge().open_pull_request(
            title="t", body="b", source_branch="nope", target_branch="main"
        )


@respx.mock
async def test_an_unreachable_backend_is_a_forge_error_not_a_crash() -> None:
    respx.post(f"{API}/repos/R-3/pull-requests").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ForgeError, match="could not reach workinabox"):
        await forge().open_pull_request(
            title="t", body="b", source_branch="feature", target_branch="main"
        )
