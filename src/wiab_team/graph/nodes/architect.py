"""Plan the work, then create the worktrees the plan implies.

Worktrees are created here, serially, rather than inside each dev node: several
concurrent ``git worktree add`` calls in one repository race on the index lock.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from wiab_team.graph.context import RuntimeContext
from wiab_team.graph.prompts import load
from wiab_team.graph.state import TeamState
from wiab_team.logging import get_logger
from wiab_team.models.plan import Plan, TesterPolicy, WorkItem
from wiab_team.models.result import Event, TokenUsage
from wiab_team.tools.protocol import AgentRequest

log = get_logger(__name__)

# Worktree index reserved for the tester's early scaffolding pass.
TESTER_INDEX = 0


def make_architect(ctx: RuntimeContext):  # type: ignore[no-untyped-def]
    async def architect(state: TeamState) -> TeamState:
        payload = state["input"]
        role = ctx.config.role("architect")

        criteria = payload.task.acceptance_criteria
        prompt = load("architect").format(
            title=payload.task.title,
            description=payload.task.description,
            acceptance_criteria=(
                "## Acceptance criteria\n\n" + "\n".join(f"- {c}" for c in criteria)
                if criteria
                else ""
            ),
            max_devs=payload.max_devs,
        )

        result = await ctx.provider.run(
            AgentRequest(
                role="architect",
                prompt=prompt,
                workdir=ctx.worktrees.repo_path,
                model=role.model,
                effort=role.effort,
            )
        )
        usage = {
            "architect": TokenUsage(
                input_tokens=result.input_tokens, output_tokens=result.output_tokens
            )
        }

        if not result.succeeded:
            return {
                "error": f"architect failed: {result.error}",
                "token_usage": usage,
                "events": [Event(node="architect", message=f"failed: {result.error}")],
            }

        plan = _parse_plan(result.text, max_devs=payload.max_devs)
        if plan is None:
            return {
                "error": "architect did not return a usable plan",
                "token_usage": usage,
                "events": [Event(node="architect", message="unparseable plan")],
            }

        branches: dict[int, str] = {}
        for index, _ in enumerate(plan.work_items, start=1):
            worktree = await ctx.worktrees.create_worktree(index)
            branches[index] = worktree.branch
        if plan.tester.can_start_early:
            worktree = await ctx.worktrees.create_worktree(TESTER_INDEX)
            branches[TESTER_INDEX] = worktree.branch

        log.info(
            "planned",
            work_items=len(plan.work_items),
            tester_starts_early=plan.tester.can_start_early,
        )
        return {
            "plan": plan,
            "dev_branches": branches,
            "token_usage": usage,
            "events": [
                Event(
                    node="architect",
                    message=f"planned {len(plan.work_items)} work item(s); "
                    f"tester starts early: {plan.tester.can_start_early}",
                )
            ],
        }

    return architect


def _parse_plan(text: str, *, max_devs: int) -> Plan | None:
    """Parse the architect's JSON, tolerating a code fence or surrounding prose."""
    raw = _extract_json(text)
    if raw is None:
        return None
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        log.warning("plan_validation_failed", error=str(exc))
        return _salvage(raw, max_devs=max_devs)

    if len(plan.work_items) > max_devs:
        # The cap is the operator's, not the architect's, to move.
        log.warning("plan_truncated", planned=len(plan.work_items), max_devs=max_devs)
        plan = plan.model_copy(update={"work_items": plan.work_items[:max_devs]})
    return plan


def _salvage(raw: dict[str, Any], *, max_devs: int) -> Plan | None:
    """Last resort: keep whatever work items are individually valid.

    An architect that gets one field wrong should not cost the whole run.
    """
    items: list[WorkItem] = []
    for index, entry in enumerate(raw.get("work_items") or [], start=1):
        if not isinstance(entry, dict):
            continue
        instruction = entry.get("instruction") or entry.get("title")
        if not instruction:
            continue
        items.append(
            WorkItem(
                id=str(entry.get("id") or f"w{index}"),
                title=str(entry.get("title") or f"Work item {index}"),
                instruction=str(instruction),
                paths=[str(p) for p in (entry.get("paths") or []) if isinstance(p, str)],
            )
        )
    if not items:
        return None
    return Plan(
        summary=str(raw.get("summary") or ""),
        work_items=items[:max_devs],
        tester=TesterPolicy(),
        test_command=str(raw.get("test_command") or ""),
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    """Find the outermost JSON object in a reply that may carry prose or a fence."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("```")[1]
        candidate = candidate.removeprefix("json").strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
