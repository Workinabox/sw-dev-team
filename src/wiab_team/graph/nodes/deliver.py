"""Publish the work branch, then settle the run's final status."""

from __future__ import annotations

from wiab_team.graph.context import RuntimeContext
from wiab_team.graph.nodes.integrate import latest_dev_results
from wiab_team.graph.state import TeamState
from wiab_team.logging import get_logger
from wiab_team.models.result import Event, RunStatus

log = get_logger(__name__)


def make_deliver(ctx: RuntimeContext):  # type: ignore[no-untyped-def]
    async def deliver(state: TeamState) -> TeamState:
        payload = state["input"]
        title, body = describe(state)

        report = await ctx.delivery.deliver(
            payload=payload,
            worktrees=ctx.worktrees,
            title=title,
            body=body,
        )
        log.info(
            "delivered",
            strategy=report.strategy,
            pushed=report.pushed,
            pull_request=report.pull_request_id,
        )
        message = (
            f"delivery via {report.strategy} failed: {report.error}"
            if report.error
            else f"delivered via {report.strategy}"
        )
        return {"delivery": report, "events": [Event(node="deliver", message=message)]}

    return deliver


def make_report(ctx: RuntimeContext):  # type: ignore[no-untyped-def]
    """Decide the final status. Nothing else writes it."""

    async def report(state: TeamState) -> TeamState:
        integration = state.get("integration")
        tests = state.get("test_reports", [])
        delivery = state.get("delivery")
        results = latest_dev_results(state.get("dev_results", []))

        landed = bool(integration and integration.merged_branches)
        unresolved = bool(integration and integration.conflicts)
        tests_failed = bool(tests and not tests[-1].passed)
        dev_failed = any(not r.succeeded for r in results)
        delivery_failed = bool(delivery and delivery.error)

        if not landed:
            status = RunStatus.FAILED
        elif unresolved or tests_failed or dev_failed or delivery_failed:
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.SUCCEEDED

        await ctx.worktrees.cleanup()
        log.info("run_finished", status=status.value)
        return {
            "status": status,
            "events": [Event(node="report", message=f"status: {status.value}")],
        }

    return report


def describe(state: TeamState) -> tuple[str, str]:
    """Build the PR title and body from what actually happened."""
    payload = state["input"]
    plan = state.get("plan")
    results = latest_dev_results(state.get("dev_results", []))
    tests = state.get("test_reports", [])
    integration = state.get("integration")

    lines: list[str] = []
    if plan and plan.summary:
        lines += [plan.summary, ""]

    if payload.task.description:
        lines += ["## Task", "", payload.task.description, ""]

    if results:
        lines += ["## Changes", ""]
        for result in results:
            status = "ok" if result.succeeded else f"failed: {result.error}"
            summary = result.summary or "no summary"
            lines.append(f"- **{result.work_item_id}** ({status}) — {summary}")
        lines.append("")

    if integration and integration.conflicts:
        lines += ["## Unresolved conflicts", ""]
        lines += [
            f"- `{c.work_item_id}` on {', '.join(c.paths) or 'unknown paths'}"
            for c in integration.conflicts
        ]
        lines.append("")

    if tests:
        last = tests[-1]
        verdict = "passed" if last.passed else "failed"
        command = f" (`{last.command}`)" if last.command else ""
        lines += ["## Tests", "", f"{verdict}{command}", ""]

    lines += ["---", "", f"Produced by the workinabox agent team, run `{payload.run_id}`."]
    return (payload.task.title, "\n".join(lines))
