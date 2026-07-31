from __future__ import annotations

from pathlib import Path

import pytest

from wiab_team.graph import prompts


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    prompts.load.cache_clear()


def test_every_known_prompt_ships_with_the_package() -> None:
    for name in prompts.KNOWN_PROMPTS:
        assert prompts.load(name).strip(), f"{name}.md is missing or empty"


def test_an_override_wins_over_the_packaged_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dev.md").write_text("custom dev prompt")
    monkeypatch.setenv(prompts.PROMPTS_DIR_ENV, str(tmp_path))

    assert prompts.load("dev") == "custom dev prompt"
    # Anything not overridden still comes from the package.
    assert "architect" in prompts.load("architect").lower()


def test_a_misspelt_override_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The silent failure worth catching: the edit appears to do nothing."""
    (tmp_path / "developer.md").write_text("oops")
    monkeypatch.setenv(prompts.PROMPTS_DIR_ENV, str(tmp_path))

    warnings = prompts.validate_overrides()
    assert len(warnings) == 1
    assert "developer.md" in warnings[0]


def test_a_correctly_named_override_is_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tester_final.md").write_text("fine")
    monkeypatch.setenv(prompts.PROMPTS_DIR_ENV, str(tmp_path))
    assert prompts.validate_overrides() == []


def test_a_missing_override_directory_is_reported_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(prompts.PROMPTS_DIR_ENV, str(tmp_path / "nope"))
    assert len(prompts.validate_overrides()) == 1
    # Loading still works, on the packaged defaults.
    assert prompts.load("dev").strip()


def test_no_override_configured_is_silent() -> None:
    assert prompts.validate_overrides() == []
    assert prompts.override_dir() is None


def test_prompts_render_with_the_fields_the_nodes_supply() -> None:
    """Guards the split brackets in the architect's JSON example.

    That prompt contains literal `{{` / `}}` so `.format` leaves the JSON intact;
    a stray single brace there raises at run time, inside a paid agent call.
    """
    prompts.load("architect").format(
        title="t", description="d", acceptance_criteria="a", max_devs=3
    )
    prompts.load("dev").format(task_title="t", instruction="i", paths="", retry="")
    prompts.load("tester_scaffold").format(
        task_title="t", scaffold_instruction="s", plan_summary="p"
    )
    prompts.load("tester_final").format(
        task_title="t", acceptance_criteria="a", dev_summaries="d", test_command="c"
    )
