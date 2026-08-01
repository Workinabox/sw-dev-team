"""Every environment variable this package reads is resolved here, once, at startup.

This mirrors the backend's convention (``backend/src/config.rs``): env vars only,
no config files, a single choke point, and validation before any work begins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from wiab_team.errors import ConfigError

DEFAULT_MODEL = "claude-opus-4-8"

# The effort ladder as accepted by the Claude API. `xhigh` is the recommended
# setting for coding and agentic work on this model family.
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})

_ROLES = ("architect", "dev", "tester")

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ToolProviderKind(StrEnum):
    CLAUDE_SDK = "claude_sdk"
    STUB = "stub"


@dataclass(frozen=True, slots=True)
class RoleConfig:
    """Per-role model settings. Falls back to the global default."""

    model: str
    effort: str | None


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Which board a long-lived team pulls from, and where the code lives.

    Separate from `Config` because it is only needed by `wiab-team work`; a
    one-shot `wiab-team run` is handed everything in its payload instead.
    """

    api_url: str
    team_id: str
    board_id: str
    repo_remote: str
    base_branch: str
    poll_interval_seconds: float
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Config:
    api_key: str | None
    tool_provider: ToolProviderKind
    workspace: Path
    roles: dict[str, RoleConfig]
    dev_count: int
    # Bounded retry budgets, so a failing run terminates instead of looping.
    max_repair_rounds: int
    max_conflict_rounds: int
    checkpoint_dsn: str | None
    log_level: str
    log_json: bool
    git_token: str | None = field(default=None, repr=False)
    # The backend's own TLS certificate, so a self-signed one can be trusted rather than
    # verification disabled. `None` leaves us on the system trust store. Used by everything
    # that talks to the backend: the board client, the forge, and git.
    api_certificate_pem: str | None = None

    def role(self, name: str) -> RoleConfig:
        try:
            return self.roles[name]
        except KeyError:  # pragma: no cover - guarded by _ROLES at construction
            raise ConfigError(f"unknown role {name!r}") from None


def _env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value


def _env_int(key: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}, got {value}")
    return value


def _env_flag(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _role_config(role: str, default_model: str, default_effort: str | None) -> RoleConfig:
    prefix = f"WIAB_TEAM_{role.upper()}"
    model = _env(f"{prefix}_MODEL", default_model)
    assert model is not None  # default_model is never None
    effort = _env(f"{prefix}_EFFORT", default_effort)
    if effort is not None and effort not in _EFFORT_LEVELS:
        raise ConfigError(
            f"{prefix}_EFFORT must be one of {sorted(_EFFORT_LEVELS)}, got {effort!r}"
        )
    return RoleConfig(model=model, effort=effort)


def load_worker() -> WorkerConfig:
    """Resolve the worker-loop settings. Raises ConfigError on bad input.

    Every field is required: a team with no board to poll or no repo to clone
    cannot do anything, and failing at startup beats a container that sits
    there logging "board empty" against a URL that was never set.
    """
    values: dict[str, str] = {}
    for key, name in (
        ("WIAB_TEAM_API_URL", "api_url"),
        ("WIAB_TEAM_TEAM_ID", "team_id"),
        ("WIAB_TEAM_BOARD_ID", "board_id"),
        ("WIAB_TEAM_REPO_REMOTE", "repo_remote"),
        ("WIAB_TEAM_API_TOKEN", "token"),
    ):
        value = _env(key)
        if value is None:
            raise ConfigError(f"{key} is required to run a team worker")
        values[name] = value

    interval_raw = _env("WIAB_TEAM_POLL_INTERVAL_SECONDS", "10")
    assert interval_raw is not None
    try:
        interval = float(interval_raw)
    except ValueError:
        raise ConfigError(
            f"WIAB_TEAM_POLL_INTERVAL_SECONDS must be a number, got {interval_raw!r}"
        ) from None
    if not 0.1 <= interval <= 3600:
        raise ConfigError(
            f"WIAB_TEAM_POLL_INTERVAL_SECONDS must be between 0.1 and 3600, got {interval}"
        )

    base_branch = _env("WIAB_TEAM_BASE_BRANCH", "main")
    assert base_branch is not None

    return WorkerConfig(
        api_url=values["api_url"],
        team_id=values["team_id"],
        board_id=values["board_id"],
        repo_remote=values["repo_remote"],
        base_branch=base_branch,
        poll_interval_seconds=interval,
        token=values["token"],
    )


def load() -> Config:
    """Resolve and validate configuration. Raises ConfigError on bad input."""

    provider_raw = _env("WIAB_TEAM_TOOL_PROVIDER", ToolProviderKind.CLAUDE_SDK.value)
    assert provider_raw is not None
    try:
        provider = ToolProviderKind(provider_raw)
    except ValueError:
        valid = sorted(k.value for k in ToolProviderKind)
        raise ConfigError(
            f"WIAB_TEAM_TOOL_PROVIDER must be one of {valid}, got {provider_raw!r}"
        ) from None

    api_key = _env("ANTHROPIC_API_KEY")
    if provider is ToolProviderKind.CLAUDE_SDK and not api_key:
        raise ConfigError("ANTHROPIC_API_KEY is required when WIAB_TEAM_TOOL_PROVIDER=claude_sdk")

    default_model = _env("WIAB_TEAM_MODEL", DEFAULT_MODEL)
    assert default_model is not None
    default_effort = _env("WIAB_TEAM_EFFORT")
    if default_effort is not None and default_effort not in _EFFORT_LEVELS:
        raise ConfigError(
            f"WIAB_TEAM_EFFORT must be one of {sorted(_EFFORT_LEVELS)}, got {default_effort!r}"
        )

    workspace_raw = _env("WIAB_TEAM_WORKSPACE", "/workspace")
    assert workspace_raw is not None

    log_level = (_env("WIAB_TEAM_LOG_LEVEL", "INFO") or "INFO").upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(
            f"WIAB_TEAM_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {log_level!r}"
        )

    return Config(
        api_key=api_key,
        tool_provider=provider,
        workspace=Path(workspace_raw),
        roles={r: _role_config(r, default_model, default_effort) for r in _ROLES},
        dev_count=_env_int("WIAB_TEAM_DEV_COUNT", 3, minimum=1, maximum=8),
        max_repair_rounds=_env_int("WIAB_TEAM_MAX_REPAIR_ROUNDS", 2, minimum=0, maximum=10),
        max_conflict_rounds=_env_int("WIAB_TEAM_MAX_CONFLICT_ROUNDS", 1, minimum=0, maximum=5),
        checkpoint_dsn=_env("WIAB_TEAM_CHECKPOINT_DSN"),
        log_level=log_level,
        log_json=_env_flag("WIAB_TEAM_LOG_JSON", True),
        git_token=_env("WIAB_TEAM_GIT_TOKEN"),
        api_certificate_pem=_env("WIAB_TEAM_API_CA_PEM"),
    )
