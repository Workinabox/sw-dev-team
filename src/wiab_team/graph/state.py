"""Graph state and its reducers.

Only serializable data lives here — the provider, worktree manager, forge, and
delivery strategy are held in a ``RuntimeContext`` closed over by the nodes, so
the checkpointer never has to persist a live object.

Reducers matter: the dev nodes run concurrently, and LangGraph raises
``InvalidUpdateError`` if two of them write the same key without one. Fields
written by a single node deliberately have *no* reducer, so a concurrent write
there surfaces as an error rather than silently picking a winner.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from wiab_team.models.input import TeamRunInput
from wiab_team.models.plan import Plan, WorkItem
from wiab_team.models.result import (
    Conflict,
    DeliveryReport,
    DevResult,
    Event,
    IntegrationReport,
    RunStatus,
    TestReport,
    TokenUsage,
)


def merge_branches(left: dict[int, str], right: dict[int, str]) -> dict[int, str]:
    """Concurrent writers, disjoint keys (one entry per dev index)."""
    return {**left, **right}


def merge_usage(left: dict[str, TokenUsage], right: dict[str, TokenUsage]) -> dict[str, TokenUsage]:
    """Sum per-role token counts. Three devs all write the ``dev`` key."""
    merged = dict(left)
    for role, usage in right.items():
        existing = merged.get(role)
        merged[role] = (
            usage
            if existing is None
            else TokenUsage(
                input_tokens=existing.input_tokens + usage.input_tokens,
                output_tokens=existing.output_tokens + usage.output_tokens,
            )
        )
    return merged


class TeamState(TypedDict, total=False):
    """Nodes return partial updates: only the keys they own."""

    # Set once at entry.
    input: TeamRunInput
    work_branch: str

    # Single-writer fields (no reducer, deliberately).
    plan: Plan
    integration: IntegrationReport
    delivery: DeliveryReport
    status: RunStatus
    error: str | None
    repair_round: int
    conflict_round: int

    # Concurrent writers.
    dev_results: Annotated[list[DevResult], operator.add]
    dev_branches: Annotated[dict[int, str], merge_branches]
    test_reports: Annotated[list[TestReport], operator.add]
    conflicts: Annotated[list[Conflict], operator.add]
    token_usage: Annotated[dict[str, TokenUsage], merge_usage]
    events: Annotated[list[Event], operator.add]


class DevTask(TypedDict):
    """The payload a ``Send`` carries to one dev node."""

    index: int
    work_item: WorkItem
    plan: Plan
    input: TeamRunInput
    # Set when the dev is being sent back to fix something.
    retry_reason: str
