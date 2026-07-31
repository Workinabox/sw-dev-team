"""The result document a team run always produces.

This is the other half of the contract with the Rust backend; see
``docs/PROTOCOL.md``. It is written on every path, including failure, so the
backend never has to infer an outcome from an exit code alone.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

RESULT_SCHEMA_VERSION = 1


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    # Work landed on the branch but something downstream fell short: tests still
    # failing after the repair budget, or an unresolved merge conflict.
    PARTIAL = "partial"
    FAILED = "failed"


class Commit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sha: str
    message: str


class DevResult(BaseModel):
    """One dev agent's outcome. Devs run concurrently; these accumulate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    work_item_id: str
    branch: str
    summary: str = ""
    commits: list[Commit] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    succeeded: bool = True
    error: str | None = None


class Conflict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    work_item_id: str
    branch: str
    paths: list[str] = Field(default_factory=list)
    detail: str = ""
    resolved: bool = False


class IntegrationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    work_branch: str
    merged_branches: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)


class TestReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # "scaffold" for the optional early pass, "final" for the verification pass.
    phase: str
    command: str = ""
    passed: bool = False
    exit_code: int | None = None
    # Truncated; full output goes to the log stream, not the result document.
    output_tail: str = ""


class DeliveryReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: str
    branch: str
    pushed: bool = False
    pull_request_url: str | None = None
    pull_request_id: str | None = None
    error: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0


class Event(BaseModel):
    """An append-only trace of what happened, for humans reading report.md."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node: str
    message: str


class TeamRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = RESULT_SCHEMA_VERSION
    run_id: str
    status: RunStatus
    work_branch: str = ""
    plan_summary: str = ""
    dev_results: list[DevResult] = Field(default_factory=list)
    integration: IntegrationReport | None = None
    test_reports: list[TestReport] = Field(default_factory=list)
    delivery: DeliveryReport | None = None
    # Keyed by role: "architect", "dev", "tester".
    token_usage: dict[str, TokenUsage] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    error: str | None = None
