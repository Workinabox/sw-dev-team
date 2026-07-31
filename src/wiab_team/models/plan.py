"""The architect's output: how the work is split and whether the tester can start early."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorkItem(BaseModel):
    """One dev's assignment. ``paths`` is advisory, not enforced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    # Ids of other work items that must merge before this one, so integrate
    # can order the merges rather than merging in arbitrary order.
    depends_on: list[str] = Field(default_factory=list)


class TesterPolicy(BaseModel):
    """Whether the tester can start before the devs have produced code.

    The tester verifies the integrated whole; unit tests belong to the developers,
    so the tester is never merely waiting on those. An early start is worthwhile
    when there is real preparation to do — fixtures, a harness, or integration
    tests against behaviour the plan already pins down. The architect decides.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    can_start_early: bool = False
    scaffold_instruction: str = ""


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = ""
    work_items: list[WorkItem] = Field(min_length=1)
    tester: TesterPolicy = TesterPolicy()
    # Command the tester runs for the final verification pass.
    test_command: str = ""
