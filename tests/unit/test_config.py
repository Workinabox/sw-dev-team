from __future__ import annotations

import os

import pytest

from wiab_team.config import DEFAULT_MODEL, ToolProviderKind, load, load_worker
from wiab_team.errors import ConfigError

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an empty environment so the host's vars can't leak in."""
    for key in list(os.environ):
        if key.startswith("WIAB_TEAM_") or key == "ANTHROPIC_API_KEY":
            monkeypatch.delenv(key, raising=False)


def test_stub_provider_needs_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    cfg = load()
    assert cfg.tool_provider is ToolProviderKind.STUB
    assert cfg.api_key is None


def test_claude_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "claude_sdk")
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load()


def test_defaults_apply_to_every_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    cfg = load()
    for role in ("architect", "dev", "tester"):
        assert cfg.role(role).model == DEFAULT_MODEL
        assert cfg.role(role).effort is None


def test_per_role_overrides_beat_the_global_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    monkeypatch.setenv("WIAB_TEAM_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("WIAB_TEAM_EFFORT", "high")
    monkeypatch.setenv("WIAB_TEAM_TESTER_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("WIAB_TEAM_TESTER_EFFORT", "low")

    cfg = load()
    assert cfg.role("architect").model == "claude-opus-4-8"
    assert cfg.role("architect").effort == "high"
    assert cfg.role("tester").model == "claude-sonnet-5"
    assert cfg.role("tester").effort == "low"


def test_unknown_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "langchain")
    with pytest.raises(ConfigError, match="WIAB_TEAM_TOOL_PROVIDER"):
        load()


def test_effort_outside_the_ladder_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    monkeypatch.setenv("WIAB_TEAM_DEV_EFFORT", "extreme")
    with pytest.raises(ConfigError, match="WIAB_TEAM_DEV_EFFORT"):
        load()


@pytest.mark.parametrize("value", ["0", "9", "-1", "many"])
def test_dev_count_is_bounded(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    monkeypatch.setenv("WIAB_TEAM_DEV_COUNT", value)
    with pytest.raises(ConfigError, match="WIAB_TEAM_DEV_COUNT"):
        load()


def test_bad_global_effort_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The global default is validated too, not just the per-role overrides."""
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    monkeypatch.setenv("WIAB_TEAM_EFFORT", "ludicrous")
    with pytest.raises(ConfigError, match="WIAB_TEAM_EFFORT"):
        load()


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", False), ("false", False), ("no", False), ("1", True), ("TRUE", True), ("on", True)],
)
def test_log_json_flag_parsing(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    monkeypatch.setenv("WIAB_TEAM_LOG_JSON", value)
    assert load().log_json is expected


def test_bad_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    monkeypatch.setenv("WIAB_TEAM_LOG_LEVEL", "chatty")
    with pytest.raises(ConfigError, match="WIAB_TEAM_LOG_LEVEL"):
        load()


def test_git_token_is_not_in_the_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token must not leak into logs via an accidental repr of the config."""
    monkeypatch.setenv("WIAB_TEAM_TOOL_PROVIDER", "stub")
    monkeypatch.setenv("WIAB_TEAM_GIT_TOKEN", "super-secret-value")
    cfg = load()
    assert cfg.git_token == "super-secret-value"
    assert "super-secret-value" not in repr(cfg)


def _worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in (
        ("WIAB_TEAM_API_URL", "https://wiab.example"),
        ("WIAB_TEAM_TEAM_ID", "TM-1"),
        ("WIAB_TEAM_BOARD_ID", "B-1"),
        ("WIAB_TEAM_REPO_REMOTE", "https://wiab.example/repos/R-7.git"),
        ("WIAB_TEAM_API_TOKEN", "tok"),
    ):
        monkeypatch.setenv(key, value)


def test_worker_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _worker_env(monkeypatch)
    cfg = load_worker()
    assert cfg.board_id == "B-1"
    assert cfg.base_branch == "main"
    assert cfg.poll_interval_seconds == 10


@pytest.mark.parametrize(
    "missing",
    [
        "WIAB_TEAM_API_URL",
        "WIAB_TEAM_TEAM_ID",
        "WIAB_TEAM_BOARD_ID",
        "WIAB_TEAM_REPO_REMOTE",
        "WIAB_TEAM_API_TOKEN",
    ],
)
def test_worker_config_requires_every_field(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    # A team with no board or no repo can do nothing; failing at startup beats
    # a container that logs "board empty" against a URL nobody set.
    _worker_env(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(ConfigError, match=missing):
        load_worker()


def test_worker_config_rejects_an_unusable_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _worker_env(monkeypatch)
    monkeypatch.setenv("WIAB_TEAM_POLL_INTERVAL_SECONDS", "soon")
    with pytest.raises(ConfigError, match="must be a number"):
        load_worker()

    monkeypatch.setenv("WIAB_TEAM_POLL_INTERVAL_SECONDS", "0")
    with pytest.raises(ConfigError, match="between"):
        load_worker()
