"""Opening a pull request, independent of who hosts the repo.

Push is *not* part of this protocol: pushing is plain git and works identically
against GitHub and workinabox's smart-HTTP transport. Only PR creation differs,
so that is all a forge has to implement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PullRequest:
    id: str
    url: str


@runtime_checkable
class Forge(Protocol):
    async def open_pull_request(
        self,
        *,
        title: str,
        body: str,
        source_branch: str,
        target_branch: str,
    ) -> PullRequest: ...

    async def aclose(self) -> None: ...
