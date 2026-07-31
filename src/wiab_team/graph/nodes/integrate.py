"""Merge the dev branches into the work branch."""

from __future__ import annotations

from wiab_team.graph.context import RuntimeContext
from wiab_team.graph.state import TeamState
from wiab_team.logging import get_logger
from wiab_team.models.plan import Plan
from wiab_team.models.result import Conflict, DevResult, Event, IntegrationReport

log = get_logger(__name__)


def latest_dev_results(results: list[DevResult]) -> list[DevResult]:
    """One result per work item, keeping the most recent.

    ``dev_results`` is append-only across repair rounds, so a retried dev appears
    more than once; the last entry is the one that reflects the branch.
    """
    latest: dict[str, DevResult] = {}
    for result in results:
        latest[result.work_item_id] = result
    return list(latest.values())


def _merge_order(plan: Plan, results: list[DevResult]) -> list[DevResult]:
    """Order by the plan's declared dependencies, falling back to plan order.

    Not a full topological sort: ``depends_on`` is advisory from an LLM, and a
    cycle in it must not hang the run.
    """
    position = {item.id: index for index, item in enumerate(plan.work_items)}
    depth = {
        item.id: len([d for d in item.depends_on if d in position]) for item in plan.work_items
    }
    return sorted(
        results,
        key=lambda r: (depth.get(r.work_item_id, 0), position.get(r.work_item_id, 0)),
    )


def make_integrate(ctx: RuntimeContext):  # type: ignore[no-untyped-def]
    async def integrate(state: TeamState) -> TeamState:
        plan = state["plan"]
        results = [r for r in latest_dev_results(state.get("dev_results", [])) if r.succeeded]

        # Merges are cumulative across rounds. A later round that merges nothing
        # (because a retry produced no new commits) must not erase the record of
        # work that already landed — that is the difference between a run
        # reporting "succeeded" and reporting "failed".
        previous = state.get("integration")
        merged: list[str] = list(previous.merged_branches) if previous else []
        conflicts: list[Conflict] = []
        events: list[Event] = []

        for result in _merge_order(plan, results):
            if not result.commits:
                events.append(
                    Event(node="integrate", message=f"{result.work_item_id}: nothing to merge")
                )
                continue
            outcome = await ctx.worktrees.merge_into_work(result.branch)
            if outcome.merged:
                if result.branch not in merged:
                    merged.append(result.branch)
                events.append(Event(node="integrate", message=f"merged {result.work_item_id}"))
            else:
                conflicts.append(
                    Conflict(
                        work_item_id=result.work_item_id,
                        branch=result.branch,
                        paths=list(outcome.conflicted_paths),
                        detail=outcome.detail,
                    )
                )
                events.append(
                    Event(
                        node="integrate",
                        message=f"conflict on {result.work_item_id}: "
                        f"{', '.join(outcome.conflicted_paths) or 'unknown paths'}",
                    )
                )

        log.info("integrated", merged=len(merged), conflicts=len(conflicts))
        return {
            "integration": IntegrationReport(
                work_branch=ctx.worktrees.work_branch,
                merged_branches=merged,
                conflicts=conflicts,
            ),
            "conflict_round": state.get("conflict_round", 0) + 1,
            "events": events,
        }

    return integrate
