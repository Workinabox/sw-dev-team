"""The client for the workinabox backend's board and task API.

**This module is the only place that knows those routes**, so if the backend's
API moves, this file is the blast radius — the same arrangement as
``forge/workinabox.py`` has for pull requests.

The surface, as implemented in the backend's ``http_api.rs``::

    GET  /teams/{team_id}                               -> TeamSnapshot
    POST /boards/{board_id}/tasks/claim  { "team_id" }  -> TaskSnapshot | 404
    GET  /works/{work_id}                               -> WorkSnapshot
    POST /tasks/{task_id}/start                         -> TaskSnapshot
    POST /tasks/{task_id}/complete                      -> TaskSnapshot
    POST /tasks/{task_id}/fail      { "reason" }        -> TaskSnapshot
    POST /tasks/{task_id}/escalate  { "reason" }        -> TaskSnapshot

Auth is ``Authorization: Bearer <token>`` — the same access token that
authenticates the git clone as the HTTP Basic password, so one credential
covers both the queue and the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wiab_team.errors import TeamError
from wiab_team.logging import get_logger

log = get_logger(__name__)

# A claim is a poll: it must not sit on a connection for the whole idle period.
CLAIM_TIMEOUT_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 30.0


class BackendError(TeamError):
    """The backend was unreachable, or answered in a way we cannot act on."""


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    """A task this team now holds, joined with the work it points at."""

    task_id: str
    work_id: str
    title: str
    description: str
    acceptance_criteria: list[str]


class BackendClient:
    """Talks to one workinabox backend as one team."""

    def __init__(self, *, api_url: str, team_id: str, token: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._team_id = team_id
        self._token = token
        self._client: Any = None

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        """One request, one place to turn a transport or status failure into a
        `BackendError`. Returns `None` for a tolerated 404."""
        import httpx

        url = f"{self._api_url}{path}"
        try:
            response = await self._http().request(method, url, json=json, timeout=timeout)
        except httpx.HTTPError as exc:
            raise BackendError(f"could not reach the backend at {url}: {exc}") from exc

        if response.status_code == 404 and allow_404:
            return None
        if response.status_code >= 400:
            raise BackendError(
                f"backend rejected {method} {path} ({response.status_code}): {response.text[:500]}"
            )
        result: dict[str, Any] = response.json()
        return result

    async def team_state(self) -> str | None:
        """This team's lifecycle state, or `None` if the backend does not know it.

        The team asks about itself rather than being told: it already polls the board, so
        this needs no second transport, and a team that cannot reach the backend keeps
        whatever it was doing instead of guessing.
        """
        team = await self._request("GET", f"/teams/{self._team_id}", allow_404=True)
        return None if team is None else str(team["state"])

    async def held_task(self) -> ClaimedTask | None:
        """The task this team already holds, if any.

        A team that was stopped mid-issue still owns its task. On restart it must pick that
        up again rather than claim a new one, or the old task would sit `in_progress`
        forever with nobody working on it.
        """
        tasks = await self._request("GET", f"/teams/{self._team_id}/task", allow_404=True)
        if tasks is None:
            return None
        return await self._with_work(tasks)

    async def claim_next(self, board_id: str) -> ClaimedTask | None:
        """Take the next task off the board, or `None` if it is empty.

        An empty board answers 404, the same as a board that does not exist —
        a polling team treats both the same way, so this does not distinguish
        them.
        """
        task = await self._request(
            "POST",
            f"/boards/{board_id}/tasks/claim",
            json={"team_id": self._team_id},
            timeout=CLAIM_TIMEOUT_SECONDS,
            allow_404=True,
        )
        if task is None:
            return None
        return await self._with_work(task)

    async def _with_work(self, task: dict[str, Any]) -> ClaimedTask:
        """Join a task to the work it points at — what the team actually needs to do."""
        task_id = str(task["id"])
        work_id = str(task["work_id"])
        work = await self._request("GET", f"/works/{work_id}")
        if work is None:  # pragma: no cover - _request only returns None for allow_404
            raise BackendError(f"work {work_id} vanished between claim and read")

        return ClaimedTask(
            task_id=task_id,
            work_id=work_id,
            title=str(work.get("title", "")),
            description=str(work.get("description", "")),
            # Only the criteria still outstanding: a fulfilled done is already
            # satisfied, and re-stating it as a requirement invites rework.
            acceptance_criteria=[
                str(done["criterion"])
                for done in work.get("dones", [])
                if not done.get("fulfilled", False)
            ],
        )

    async def start(self, task_id: str) -> None:
        await self._request("POST", f"/tasks/{task_id}/start")

    async def complete(self, task_id: str) -> None:
        await self._request("POST", f"/tasks/{task_id}/complete")

    async def fail(self, task_id: str, reason: str) -> None:
        await self._request("POST", f"/tasks/{task_id}/fail", json={"reason": reason})

    async def escalate(self, task_id: str, reason: str) -> None:
        await self._request("POST", f"/tasks/{task_id}/escalate", json={"reason": reason})

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
