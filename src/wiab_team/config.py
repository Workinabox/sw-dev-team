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
    )
