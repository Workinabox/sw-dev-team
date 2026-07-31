"""GitHub pull requests via the REST API."""

from __future__ import annotations

from typing import Any

from wiab_team.errors import ForgeError
from wiab_team.forge.protocol import PullRequest

API_ROOT = "https://api.github.com"


class GitHubForge:
    def __init__(self, *, repo: str, token: str, api_root: str = API_ROOT) -> None:
        self._repo = repo
        self._token = token
        self._api_root = api_root.rstrip("/")
        self._client: Any = None

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
            )
        return self._client

    async def open_pull_request(
        self, *, title: str, body: str, source_branch: str, target_branch: str
    ) -> PullRequest:
        import httpx

        url = f"{self._api_root}/repos/{self._repo}/pulls"
        try:
            response = await self._http().post(
                url,
                json={
                    "title": title,
                    "body": body,
                    "head": source_branch,
                    "base": target_branch,
                },
            )
        except httpx.HTTPError as exc:
            raise ForgeError(f"could not reach GitHub: {exc}") from exc

        if response.status_code >= 400:
            raise ForgeError(
                f"GitHub rejected the pull request ({response.status_code}): {response.text[:500]}"
            )

        payload = response.json()
        return PullRequest(id=str(payload.get("number", "")), url=str(payload.get("html_url", "")))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
