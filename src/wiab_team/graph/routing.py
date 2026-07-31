"""Conditional-edge functions: the fan-out and the two bounded loops.

Kept out of the nodes so the control flow can be read, and tested, in one place.
"""

from __future__ import annotations

from langgraph.graph import END
from langgraph.types import Send

from wiab_team.config import Config
from wiab_team.graph.nodes.integrate import latest_dev_results
from wiab_team.graph.state import TeamState


def fan_out(state: TeamState) -> list[Send] | str:
    """One dev per work item, plus the tester if the plan lets it start early.

    Returning ``END`` short-circuits a run whose architect produced nothing —
    there is no work to hand out and no branch to deliver.
    """
    plan = state.get("plan")
    if plan is None:
        return END

    sends = [
        Send(
            "dev",
            {
                "index": index,
                "work_item": item,
                "plan": plan,
                "input": state["input"],
                "retry_reason": "",
            },
        )
        for index, item in enumerate(plan.work_items, start=1)
    ]
    if plan.tester.can_start_early:
        sends.append(Send("tester_scaffold", state))
    return sends


def after_integrate(config: Config):  # type: ignore[no-untyped-def]
    """Send conflicted devs back to fix their merge, up to the configured budget.

    Past the budget the conflict is recorded rather than retried forever: a
    branch with a documented conflict is more useful than a run that never ends.
    """

    def route(state: TeamState) -> list[Send] | str:
        integration = state.get("integration")
        if integration is None or not integration.conflicts:
            return "tester_final"
        if state.get("conflict_round", 0) > config.max_conflict_rounds:
            return "tester_final"

        plan = state["plan"]
        by_id = {item.id: item for item in plan.work_items}
        indexes = {item.id: index for index, item in enumerate(plan.work_items, start=1)}

        sends = [
            Send(
                "dev",
                {
                    "index": indexes[conflict.work_item_id],
                    "work_item": by_id[conflict.work_item_id],
                    "plan": plan,
                    "input": state["input"],
                    "retry_reason": (
                        "Your branch conflicts with work already merged from another "
                        f"developer, in: {', '.join(conflict.paths) or 'unknown files'}. "
                        "Your worktree has been updated to the current branch state. "
                        "Re-apply your change on top of it, keeping the other "
                        "developer's work intact."
                    ),
                },
            )
            for conflict in integration.conflicts
            if conflict.work_item_id in by_id
        ]
        return sends or "tester_final"

    return route


def after_tests(config: Config):  # type: ignore[no-untyped-def]
    """Send every dev back once when the suite fails, up to the repair budget."""

    def route(state: TeamState) -> list[Send] | str:
        reports = state.get("test_reports", [])
        if not reports or reports[-1].passed:
            return "deliver"
        if state.get("repair_round", 0) > config.max_repair_rounds:
            return "deliver"

        plan = state["plan"]
        failure = reports[-1].output_tail
        succeeded = {r.work_item_id for r in latest_dev_results(state.get("dev_results", []))}

        return [
            Send(
                "dev",
                {
                    "index": index,
                    "work_item": item,
                    "plan": plan,
                    "input": state["input"],
                    "retry_reason": (
                        "The test suite is failing on the integrated branch. "
                        "Fix what falls within your assignment; if the failure is "
                        "not yours, say so and change nothing.\n\n"
                        f"```\n{failure}\n```"
                    ),
                },
            )
            for index, item in enumerate(plan.work_items, start=1)
            if item.id in succeeded
        ] or "deliver"

    return route
