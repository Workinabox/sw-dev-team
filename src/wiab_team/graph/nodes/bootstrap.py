"""Prepare the workspace: clone the repo and cut the work branch."""

from __future__ import annotations

from wiab_team.graph.context import RuntimeContext
from wiab_team.graph.state import TeamState
from wiab_team.logging import get_logger
from wiab_team.models.result import Event, RunStatus

log = get_logger(__name__)


def make_bootstrap(ctx: RuntimeContext):  # type: ignore[no-untyped-def]
    async def bootstrap(state: TeamState) -> TeamState:
        payload = state["input"]
        log.info(
            "bootstrap",
            run_id=payload.run_id,
            remote=ctx.worktrees.describe_remote(),
            base_branch=payload.repo.base_branch,
        )
        await ctx.worktrees.clone()
        return {
            "work_branch": ctx.worktrees.work_branch,
            "status": RunStatus.FAILED,  # until something succeeds, assume it didn't
            "repair_round": 0,
            "conflict_round": 0,
            "events": [
                Event(
                    node="bootstrap",
                    message=f"cloned {ctx.worktrees.describe_remote()} "
                    f"and cut {ctx.worktrees.work_branch}",
                )
            ],
        }

    return bootstrap
