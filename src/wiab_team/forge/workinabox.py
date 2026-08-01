"""Pull requests against a workinabox-hosted repo.

**This module is the only place that knows anything about workinabox's pull
request API**, so if that API moves, this file is the blast radius.

The surface, as implemented in the backend's `http_api.rs`:

    POST /repos/{repo_id}/pull-requests
      { "title", "description", "source_branch", "target_branch" }
      -> PullRequestSnapshot { "id": "PR-7", "state": "open", ... }
    GET  /pull-requests/{id}
    POST /pull-requests/{id}/close
    POST /pull-requests/{id}/merge

Auth is ``Authorization: Bearer <token>`` — the same access token that
authenticates the git push as the HTTP Basic password, so one credential covers
both halves of delivery.
"""

from __future__ import annotations

from typing import Any

from wiab_team.errors import ForgeError
from wiab_team.forge.protocol import PullRequest
from wiab_team.logging import get_logger
from wiab_team.tls import verification_for

log = get_logger(__name__)


class WorkinaboxForge:
    def __init__(
        self,
        *,
        api_url: str,
        repo_id: str,
        token: str,
        certificate_pem: str | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._repo_id = repo_id
        self._token = token
        self._certificate_pem = certificate_pem
        self._client: Any = None

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
                verify=verification_for(self._certificate_pem),
            )
        return self._client

    async def open_pull_request(
        self, *, title: str, body: str, source_branch: str, target_branch: str
    ) -> PullRequest:
        import httpx

        url = f"{self._api_url}/repos/{self._repo_id}/pull-requests"
        try:
            response = await self._http().post(
                url,
                json={
                    "title": title,
                    "description": body,
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                },
            )
        except httpx.HTTPError as exc:
            raise ForgeError(f"could not reach workinabox at {self._api_url}: {exc}") from exc

        if response.status_code == 404:
            # Either the repo is unknown, or the backend predates the pull request
            # routes. Both leave a pushed branch that a human can still merge, so
            # say that rather than a bare "not found".
            raise ForgeError(
                f"workinabox returned 404 for {url} — the repo may not exist, or the "
                "backend may predate the pull request routes; the branch was pushed "
                "and can be merged manually"
            )
        if response.status_code >= 400:
            raise ForgeError(
                f"workinabox rejected the pull request ({response.status_code}): "
                f"{response.text[:500]}"
            )

        payload = response.json()
        pull_request_id = str(payload.get("id", ""))
        return PullRequest(
            id=pull_request_id,
            url=f"{self._api_url}/pull-requests/{pull_request_id}",
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
