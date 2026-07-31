"""A thin async wrapper around the git CLI.

We shell out rather than use a binding: the backend's own `GitBackend` has no
clone/push/merge (see `backend/crates/wiab-core/src/repo/git_backend.rs`), and the
agents themselves run shell commands anyway, so `git` is already a hard dependency
of the sandbox.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from wiab_team.errors import GitError

# Never prompt: a hung credential prompt inside a sandbox looks like a stuck run.
_NON_INTERACTIVE = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "true",
    "GCM_INTERACTIVE": "never",
}


@dataclass(frozen=True, slots=True)
class GitResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


async def run_git(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> GitResult:
    """Run a git command. Raises GitError on failure unless ``check`` is False."""
    process_env = {**os.environ, **_NON_INTERACTIVE, **(env or {})}
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        env=process_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    result = GitResult(
        exit_code=process.returncode or 0,
        stdout=out.decode("utf-8", "replace"),
        stderr=err.decode("utf-8", "replace"),
    )
    if check and not result.ok:
        raise GitError(" ".join(args), result.exit_code, result.stderr)
    return result


def redact(url: str) -> str:
    """Strip credentials from a remote URL so it is safe to log or store."""
    if "@" not in url or "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host_and_path = rest.rpartition("@")
    return f"{scheme}://{host_and_path}"


def authenticated_remote(url: str, token: str | None) -> str:
    """Embed a token in an https remote for a single command's use.

    Workinabox's smart-HTTP transport authenticates with HTTP Basic where the
    *password* is the access token and the username is ignored; GitHub accepts the
    same shape for a PAT. Only ever pass the result to a single git invocation —
    never write it into the repo's config, or it lands in `.git/config` on disk.
    """
    if not token or not url.startswith("https://"):
        return url
    if "@" in url.split("://", 1)[1].split("/", 1)[0]:
        return url  # already carries credentials; leave it alone
    return url.replace("https://", f"https://x-access-token:{token}@", 1)
