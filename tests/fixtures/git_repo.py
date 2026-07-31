"""Build throwaway git repositories for tests."""

from __future__ import annotations

from pathlib import Path

from wiab_team.vcs.git import run_git


async def make_origin(tmp_path: Path, *, files: dict[str, str] | None = None) -> Path:
    """Create a bare repo with one commit on `main`, and return its path.

    Mirrors how workinabox hosts repos: a bare repo whose HEAD is `main`.
    """
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir(parents=True)

    await run_git("init", "--bare", "--initial-branch=main", str(origin))
    await run_git("init", "--initial-branch=main", str(seed))
    await run_git("config", "user.name", "seed", cwd=seed)
    await run_git("config", "user.email", "seed@example.com", cwd=seed)

    for name, content in (files or {"README.md": "seed\n"}).items():
        target = seed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    await run_git("add", "-A", cwd=seed)
    await run_git("commit", "-m", "seed", cwd=seed)
    await run_git("push", str(origin), "main", cwd=seed)
    return origin
