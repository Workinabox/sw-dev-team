"""The library surface. The CLI is a thin wrapper over this.

Deliberately transport-agnostic: whatever eventually invokes this — a CLI, an
HTTP server, a queue consumer — calls ``run_team`` with a parsed payload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wiab_team.config import Config
from wiab_team.config import load as load_config
from wiab_team.delivery.artifacts import write as write_artifacts
from wiab_team.delivery.protocol import DeliveryStrategy
from wiab_team.graph.builder import compile_graph
from wiab_team.graph.context import RuntimeContext
from wiab_team.graph.state import TeamState
from wiab_team.logging import get_logger
from wiab_team.models.input import TeamRunInput
from wiab_team.models.result import RunStatus, TeamRunResult
from wiab_team.tools.protocol import ToolProvider
from wiab_team.tools.registry import build_provider
from wiab_team.vcs.worktrees import WorktreeManager

log = get_logger(__name__)

# LangGraph counts every node execution; the retry loops make the default of 25
# too tight for a three-dev run that repairs once.
RECURSION_LIMIT = 100


async def run_team(
    payload: TeamRunInput,
    *,
    config: Config | None = None,
    provider: ToolProvider | None = None,
    delivery: DeliveryStrategy | None = None,
    checkpointer: Any | None = None,
    workspace: Path | None = None,
) -> TeamRunResult:
    """Run the team to completion and return its result.

    Every override exists for testing: pass a scripted provider and a temp
    workspace and the whole graph runs with no API key and no network.
    """
    config = config or load_config()
    workspace = workspace or config.workspace
    provider = provider or build_provider(config)
    delivery = delivery or _build_delivery(payload, config)

    from wiab_team.graph.prompts import override_dir, validate_overrides

    for warning in validate_overrides():
        log.warning("prompt_override", detail=warning)
    if (directory := override_dir()) is not None:
        log.info("prompt_overrides_active", directory=str(directory))

    worktrees = WorktreeManager(
        workspace=workspace,
        run_id=payload.run_id,
        remote=payload.repo.remote,
        base_branch=payload.repo.base_branch,
        token=config.git_token,
    )
    ctx = RuntimeContext(config=config, provider=provider, worktrees=worktrees, delivery=delivery)

    graph = compile_graph(ctx, checkpointer=checkpointer)
    initial: TeamState = {"input": payload}
    run_config = {
        "recursion_limit": RECURSION_LIMIT,
        "configurable": {"thread_id": payload.run_id},
    }

    try:
        final: TeamState = await graph.ainvoke(initial, config=run_config)
        result = to_result(payload, final)
    except Exception as exc:
        log.exception("run_crashed", run_id=payload.run_id)
        result = TeamRunResult(
            run_id=payload.run_id,
            status=RunStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        await provider.aclose()

    write_artifacts(result, workspace)
    return result


def to_result(payload: TeamRunInput, state: TeamState) -> TeamRunResult:
    """Project the graph's final state onto the documented result schema."""
    plan = state.get("plan")
    from wiab_team.graph.nodes.integrate import latest_dev_results

    return TeamRunResult(
        run_id=payload.run_id,
        status=state.get("status", RunStatus.FAILED),
        work_branch=state.get("work_branch", ""),
        plan_summary=plan.summary if plan else "",
        dev_results=latest_dev_results(state.get("dev_results", [])),
        integration=state.get("integration"),
        test_reports=state.get("test_reports", []),
        delivery=state.get("delivery"),
        token_usage=state.get("token_usage", {}),
        events=state.get("events", []),
        error=state.get("error"),
    )


def _build_delivery(payload: TeamRunInput, config: Config) -> DeliveryStrategy:
    from wiab_team.delivery.registry import build_delivery

    return build_delivery(payload, config)
