"""Push the work branch to the remote. Works for any forge — it is plain git."""

from __future__ import annotations

from wiab_team.errors import GitError
from wiab_team.logging import get_logger
from wiab_team.models.input import TeamRunInput
from wiab_team.models.result import DeliveryReport
from wiab_team.vcs.worktrees import WorktreeManager

log = get_logger(__name__)


class PushDelivery:
    async def deliver(
        self,
        *,
        payload: TeamRunInput,
        worktrees: WorktreeManager,
        title: str,
        body: str,
    ) -> DeliveryReport:
        try:
            await worktrees.push_work_branch()
        except GitError as exc:
            log.error("push_failed", error=str(exc))
            return DeliveryReport(
                strategy="push", branch=worktrees.work_branch, pushed=False, error=str(exc)
            )
        return DeliveryReport(strategy="push", branch=worktrees.work_branch, pushed=True)
