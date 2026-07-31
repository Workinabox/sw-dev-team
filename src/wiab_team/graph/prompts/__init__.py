"""Prompt templates, kept as files so they can be edited without touching code.

Prompts get tuned constantly, and the packaged copies live inside the installed
package — which in a container means rebuilding the image to change a word. So
``WIAB_TEAM_PROMPTS_DIR`` points at a directory of overrides: any ``<name>.md``
found there wins, and anything missing falls back to the packaged default. Mount
a directory, edit, re-run.
"""

from __future__ import annotations

import os
from functools import cache
from importlib import resources
from pathlib import Path

PROMPTS_DIR_ENV = "WIAB_TEAM_PROMPTS_DIR"

# Every template the graph loads. Used by `validate_overrides` to catch a typo in
# an override filename, which would otherwise silently fall back to the default.
KNOWN_PROMPTS = frozenset({"architect", "dev", "tester_scaffold", "tester_final"})


def override_dir() -> Path | None:
    value = os.environ.get(PROMPTS_DIR_ENV)
    return Path(value) if value else None


@cache
def load(name: str) -> str:
    """Read a prompt template by stem, e.g. ``load("architect")``.

    Cached per process: a run should not re-read a prompt mid-flight and give two
    agents different instructions.
    """
    directory = override_dir()
    if directory is not None:
        override = directory / f"{name}.md"
        if override.is_file():
            return override.read_text(encoding="utf-8")
    return (resources.files(__package__) / f"{name}.md").read_text(encoding="utf-8")


def validate_overrides() -> list[str]:
    """Warnings about an override directory, as human-readable strings.

    A misspelt filename is the failure worth catching: the run proceeds happily
    on the default prompt and the edit appears to have done nothing.
    """
    directory = override_dir()
    if directory is None:
        return []
    if not directory.is_dir():
        return [f"{PROMPTS_DIR_ENV}={directory} is not a directory; using defaults"]

    warnings = []
    for path in sorted(directory.glob("*.md")):
        if path.stem not in KNOWN_PROMPTS:
            warnings.append(
                f"{path.name} is not a known prompt and will be ignored "
                f"(expected one of: {', '.join(sorted(KNOWN_PROMPTS))})"
            )
    return warnings
