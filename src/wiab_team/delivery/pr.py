"""Push, then open a pull request through the configured forge."""

from __future__ import annotations

from wiab_team.errors import ForgeError, GitError
from wiab_team.forge.protocol import Forge
from wiab_team.logging import get_logger
from wiab_team.models.input import TeamRunInput
from wiab_team.models.result import DeliveryReport
from wiab_team.vcs.worktrees import WorktreeManager

log = get_logger(__name__)


class PullRequestDelivery:
    def __init__(self, forge: Forge) -> None:
        self._forge = forge

    async def deliver(
        self,
        *,
        payload: TeamRunInput,
        worktrees: WorktreeManager,
        title: str,
        body: str,
    ) -> DeliveryReport:
        branch = worktrees.work_branch
        try:
            await worktrees.push_work_branch()
        except GitError as exc:
            log.error("push_failed", error=str(exc))
            return DeliveryReport(
                strategy="pr", branch=branch, pushed=False, error=f"push failed: {exc}"
            )

        try:
            pull_request = await self._forge.open_pull_request(
                title=title,
                body=body,
                source_branch=branch,
                target_branch=payload.repo.base_branch,
            )
        except ForgeError as exc:
            # The branch is pushed and usable; only the PR is missing. That is a
            # partial outcome worth reporting, not a lost run.
            log.error("open_pull_request_failed", error=str(exc))
            return DeliveryReport(
                strategy="pr", branch=branch, pushed=True, error=f"pull request failed: {exc}"
            )
        finally:
            await self._forge.aclose()

        return DeliveryReport(
            strategy="pr",
            branch=branch,
            pushed=True,
            pull_request_url=pull_request.url,
            pull_request_id=pull_request.id,
        )
