"""Worktree lifecycle for a team run.

Layout inside the sandbox workspace:

    <workspace>/
      repo/                  the clone; holds the work branch wiab/<run>
      worktrees/dev-1/       worktree on wiab/<run>-dev-1
      worktrees/dev-2/       ...

Each dev gets its own checkout, so three agents can edit files concurrently
without a shared index. Integration merges the dev branches back into the work
branch in the main clone.
"""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from wiab_team.errors import GitError
from wiab_team.logging import get_logger
from wiab_team.models.result import Commit
from wiab_team.vcs.git import authenticated_remote, redact, run_git

# Identity for commits this system creates. Agents don't get to choose it.
BOT_NAME = "workinabox agent team"
BOT_EMAIL = "agents@workinabox.local"

log = get_logger(__name__)

BRANCH_PREFIX = "wiab"


def work_branch(run_id: str) -> str:
    return f"{BRANCH_PREFIX}/{run_id}"


def dev_branch(run_id: str, index: int) -> str:
    """Dev branches are siblings of the work branch, not children of it.

    ``wiab/<run>/dev-1`` would require ``refs/heads/wiab/<run>`` to be both a ref
    and a directory, which git rejects outright. The separator has to be a dash.
    """
    return f"{BRANCH_PREFIX}/{run_id}-dev-{index}"


@dataclass(frozen=True, slots=True)
class Worktree:
    """One dev's checkout."""

    index: int
    branch: str
    path: Path


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    branch: str
    merged: bool
    conflicted_paths: tuple[str, ...] = ()
    detail: str = ""


class WorktreeManager:
    """Owns the clone and the per-dev worktrees for a single run."""

    def __init__(
        self,
        *,
        workspace: Path,
        run_id: str,
        remote: str,
        base_branch: str = "main",
        token: str | None = None,
    ) -> None:
        self._workspace = workspace
        self._run_id = run_id
        self._remote = remote
        self._base_branch = base_branch
        self._token = token
        self._worktrees: dict[int, Worktree] = {}

    @property
    def repo_path(self) -> Path:
        return self._workspace / "repo"

    @property
    def work_branch(self) -> str:
        return work_branch(self._run_id)

    @property
    def worktrees(self) -> dict[int, Worktree]:
        return dict(self._worktrees)

    async def adopt_existing(self) -> dict[int, Worktree]:
        """Rediscover this run's worktrees from the clone on disk.

        The manager holds its worktrees in memory, but a resumed run gets a new
        manager while the worktrees themselves are still on disk. Without this,
        every dev on a resumed run would find nothing prepared and give up —
        which is exactly what happened before it existed.

        Only this run's branches are adopted; another run's worktrees under the
        same workspace are none of our business.
        """
        if not self.repo_path.exists():
            return {}
        listing = (await run_git("worktree", "list", "--porcelain", cwd=self.repo_path)).stdout
        prefix = f"{BRANCH_PREFIX}/{self._run_id}-dev-"
        path: Path | None = None
        for line in listing.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree ").strip())
            elif line.startswith("branch ") and path is not None:
                branch = line.removeprefix("branch ").strip().removeprefix("refs/heads/")
                if branch.startswith(prefix):
                    suffix = branch.removeprefix(prefix)
                    if suffix.isdigit():
                        index = int(suffix)
                        self._worktrees[index] = Worktree(index=index, branch=branch, path=path)
                path = None
        if self._worktrees:
            log.info("worktrees_adopted", indexes=sorted(self._worktrees))
        return dict(self._worktrees)

    async def clone(self) -> None:
        """Clone the remote and cut the work branch off the base branch.

        Starts from a clean slate. A long-lived team runs many issues in one
        container, and the previous issue's clone is still there — cleanup only
        removes worktrees, on the old assumption that the sandbox went away after
        one run. Resuming does not come through here, so a checkpointed run's
        clone is never destroyed.
        """
        self._workspace.mkdir(parents=True, exist_ok=True)
        for leftover in (self.repo_path, self._workspace / "worktrees"):
            if leftover.exists():
                log.info("clearing_previous_run", path=str(leftover))
                shutil.rmtree(leftover, ignore_errors=True)
        await run_git(
            "clone",
            "--branch",
            self._base_branch,
            authenticated_remote(self._remote, self._token),
            str(self.repo_path),
        )
        # Drop the credential-bearing URL from .git/config immediately; it is
        # re-supplied per push instead of persisting on disk.
        await run_git("remote", "set-url", "origin", self._remote, cwd=self.repo_path)
        await self._configure_identity(self.repo_path)
        await run_git("checkout", "-b", self.work_branch, cwd=self.repo_path)

    async def _configure_identity(self, path: Path) -> None:
        await run_git("config", "user.name", BOT_NAME, cwd=path)
        await run_git("config", "user.email", BOT_EMAIL, cwd=path)

    async def create_worktree(self, index: int) -> Worktree:
        """Add a worktree on a fresh branch cut from the work branch."""
        branch = dev_branch(self._run_id, index)
        path = self._workspace / "worktrees" / f"dev-{index}"
        path.parent.mkdir(parents=True, exist_ok=True)
        await run_git(
            "worktree",
            "add",
            "-b",
            branch,
            str(path),
            self.work_branch,
            cwd=self.repo_path,
        )
        await self._configure_identity(path)
        worktree = Worktree(index=index, branch=branch, path=path)
        self._worktrees[index] = worktree
        return worktree

    async def commit_all(self, worktree: Worktree, message: str) -> Commit | None:
        """Stage and commit everything in a worktree. Returns None if nothing changed."""
        await run_git("add", "-A", cwd=worktree.path)
        status = await run_git("status", "--porcelain", cwd=worktree.path)
        if not status.stdout.strip():
            return None
        await run_git("commit", "-m", message, cwd=worktree.path)
        sha = (await run_git("rev-parse", "HEAD", cwd=worktree.path)).stdout.strip()
        return Commit(sha=sha, message=message)

    async def commits_on(self, branch: str) -> list[Commit]:
        """Commits on ``branch`` that are not on the work branch, oldest first."""
        result = await run_git(
            "log",
            "--reverse",
            "--format=%H%x00%s",
            f"{self.work_branch}..{branch}",
            cwd=self.repo_path,
        )
        commits: list[Commit] = []
        for line in result.stdout.splitlines():
            sha, _, subject = line.partition("\x00")
            if sha:
                commits.append(Commit(sha=sha, message=subject))
        return commits

    async def files_changed(self, branch: str) -> list[str]:
        result = await run_git(
            "diff", "--name-only", f"{self.work_branch}...{branch}", cwd=self.repo_path
        )
        return [line for line in result.stdout.splitlines() if line]

    async def merge_into_work(self, branch: str) -> MergeOutcome:
        """Merge a dev branch into the work branch.

        On conflict the merge is aborted, so the work branch is never left in a
        half-merged state — the caller decides whether to send the dev back to fix
        it or record it as unresolved.
        """
        await run_git("checkout", self.work_branch, cwd=self.repo_path)
        result = await run_git(
            "merge", "--no-ff", "-m", f"Merge {branch}", branch, cwd=self.repo_path, check=False
        )
        if result.ok:
            return MergeOutcome(branch=branch, merged=True)

        conflicts = await run_git(
            "diff", "--name-only", "--diff-filter=U", cwd=self.repo_path, check=False
        )
        paths = tuple(line for line in conflicts.stdout.splitlines() if line)
        await run_git("merge", "--abort", cwd=self.repo_path, check=False)
        return MergeOutcome(
            branch=branch,
            merged=False,
            conflicted_paths=paths,
            detail=(result.stderr or result.stdout).strip(),
        )

    async def begin_rebase(self, worktree: Worktree) -> tuple[str, ...]:
        """Replay a dev's commits onto the current work branch.

        On conflict the rebase is deliberately **left in progress**, with the
        markers in the files, so the agent can see both sides and reconcile them.
        Aborting here instead would hand the dev back the very commit that
        conflicts, and the retry could not possibly succeed.

        Returns the conflicted paths, or an empty tuple when the rebase was clean.
        """
        result = await run_git("rebase", self.work_branch, cwd=worktree.path, check=False)
        if result.ok:
            return ()
        conflicts = await run_git(
            "diff", "--name-only", "--diff-filter=U", cwd=worktree.path, check=False
        )
        return tuple(line for line in conflicts.stdout.splitlines() if line)

    async def rebase_in_progress(self, worktree: Worktree) -> bool:
        result = await run_git(
            "rev-parse", "--git-path", "rebase-merge", cwd=worktree.path, check=False
        )
        if not result.ok:
            return False
        marker = Path(result.stdout.strip())
        if not marker.is_absolute():
            marker = worktree.path / marker
        return marker.exists()

    async def continue_rebase(self, worktree: Worktree) -> bool:
        """Finish a rebase the agent was asked to resolve.

        Refuses to continue while conflict markers remain: git will happily commit
        them, and a branch carrying ``<<<<<<<`` into the work branch is worse than
        an honestly-reported unresolved conflict.
        """
        if await self._has_conflict_markers(worktree):
            await run_git("rebase", "--abort", cwd=worktree.path, check=False)
            return False

        await run_git("add", "-A", cwd=worktree.path)
        result = await run_git(
            "-c",
            "core.editor=true",
            "rebase",
            "--continue",
            cwd=worktree.path,
            check=False,
        )
        if result.ok:
            return True
        await run_git("rebase", "--abort", cwd=worktree.path, check=False)
        return False

    async def _has_conflict_markers(self, worktree: Worktree) -> bool:
        # -I so a file legitimately discussing markers (docs, this test suite)
        # doesn't trip it: we only care about lines git itself would have written.
        result = await run_git(
            "grep",
            "--no-index",
            "-l",
            "-E",
            r"^<<<<<<< |^>>>>>>> ",
            "--",
            ".",
            cwd=worktree.path,
            check=False,
        )
        return bool(result.stdout.strip())

    async def push_work_branch(self) -> None:
        """Push the work branch, supplying the credential for this command only."""
        await run_git(
            "push",
            authenticated_remote(self._remote, self._token),
            f"{self.work_branch}:{self.work_branch}",
            cwd=self.repo_path,
        )

    async def cleanup(self) -> None:
        """Best-effort removal of the worktrees. Never raises."""
        for worktree in self._worktrees.values():
            # Cleanup is advisory: the sandbox is torn down anyway, and failing
            # here would mask whatever actually went wrong in the run.
            with contextlib.suppress(GitError):
                await run_git(
                    "worktree", "remove", "--force", str(worktree.path), cwd=self.repo_path
                )

    def describe_remote(self) -> str:
        return redact(self._remote)
