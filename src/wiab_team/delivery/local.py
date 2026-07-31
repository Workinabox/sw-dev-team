"""Leave the work on the branch, in the sandbox. No credentials required."""

from __future__ import annotations

from wiab_team.models.input import TeamRunInput
from wiab_team.models.result import DeliveryReport
from wiab_team.vcs.worktrees import WorktreeManager


class LocalDelivery:
    async def deliver(
        self,
        *,
        payload: TeamRunInput,
        worktrees: WorktreeManager,
        title: str,
        body: str,
    ) -> DeliveryReport:
        return DeliveryReport(strategy="local", branch=worktrees.work_branch, pushed=False)
