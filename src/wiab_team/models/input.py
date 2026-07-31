"""The input payload for a team run.

This is half of the contract with the Rust backend; see ``docs/PROTOCOL.md``.
Anything added here must be reflected there and in ``PAYLOAD_SCHEMA_VERSION``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

PAYLOAD_SCHEMA_VERSION = 1


class ForgeKind(StrEnum):
    """Where the repository is hosted, and therefore how a PR is opened."""

    GITHUB = "github"
    WORKINABOX = "workinabox"
    NONE = "none"


class DeliveryKind(StrEnum):
    LOCAL = "local"
    PUSH = "push"
    PR = "pr"


class RepoRef(BaseModel):
    """Where the work happens.

    ``remote`` is a git URL the sandbox can reach. For workinabox-hosted repos
    that is ``https://<host>/repos/R-<n>.git`` (HTTP Basic, password = access
    token) or ``ssh://<host>:2222/R-<n>.git``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    remote: str = Field(min_length=1)
    base_branch: str = Field(default="main", min_length=1)
    forge: ForgeKind = ForgeKind.NONE
    # Present only for forge=workinabox: the R-<n> id the PR routes are keyed on.
    workinabox_repo_id: str | None = None
    # Present only for forge=workinabox: base URL of the backend HTTP API.
    workinabox_api_url: str | None = None
    # Present only for forge=github: "owner/name".
    github_repo: str | None = None

    @model_validator(mode="after")
    def _forge_fields_present(self) -> RepoRef:
        if self.forge is ForgeKind.WORKINABOX and not (
            self.workinabox_repo_id and self.workinabox_api_url
        ):
            raise ValueError("forge=workinabox requires workinabox_repo_id and workinabox_api_url")
        if self.forge is ForgeKind.GITHUB and not self.github_repo:
            raise ValueError("forge=github requires github_repo")
        return self


class TaskSpec(BaseModel):
    """The issue the team is asked to deliver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Opaque to us; the backend's identifier for the unit of work.
    external_id: str | None = None
    title: str = Field(min_length=1)
    description: str = ""
    # Free-form acceptance criteria, mirroring the backend's Done entities.
    acceptance_criteria: list[str] = Field(default_factory=list)


class TeamRunInput(BaseModel):
    """Everything a team run needs, independent of how it was invoked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = PAYLOAD_SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    task: TaskSpec
    repo: RepoRef
    delivery: DeliveryKind = DeliveryKind.LOCAL
    # Cap on parallel dev agents. The architect may plan fewer, never more.
    max_devs: int = Field(default=3, ge=1, le=8)

    @model_validator(mode="after")
    def _delivery_reachable(self) -> TeamRunInput:
        if self.delivery is not DeliveryKind.LOCAL and self.repo.forge is ForgeKind.NONE:
            raise ValueError(f"delivery={self.delivery} requires a forge other than 'none'")
        return self
