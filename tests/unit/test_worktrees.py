from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.git_repo import make_origin
from wiab_team.vcs import Worktree, WorktreeManager, run_git
from wiab_team.vcs.git import authenticated_remote, redact
from wiab_team.vcs.worktrees import dev_branch, work_branch

RUN = "run-1"


async def _manager(tmp_path: Path, *, files: dict[str, str] | None = None) -> WorktreeManager:
    origin = await make_origin(tmp_path, files=files)
    manager = WorktreeManager(
        workspace=tmp_path / "ws", run_id=RUN, remote=str(origin), base_branch="main"
    )
    await manager.clone()
    return manager


async def test_clone_cuts_the_work_branch(tmp_path: Path) -> None:
    manager = await _manager(tmp_path)
    head = await run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=manager.repo_path)
    assert head.stdout.strip() == work_branch(RUN) == "wiab/run-1"


async def test_clone_does_not_persist_credentials(tmp_path: Path) -> None:
    """A token must never end up in .git/config, where it outlives the command."""
    origin = await make_origin(tmp_path)
    manager = WorktreeManager(
        workspace=tmp_path / "ws",
        run_id=RUN,
        remote=str(origin),
        base_branch="main",
        token="secret-token",
    )
    await manager.clone()
    config = (manager.repo_path / ".git" / "config").read_text()
    assert "secret-token" not in config


async def test_each_dev_gets_an_independent_checkout(tmp_path: Path) -> None:
    manager = await _manager(tmp_path)
    one = await manager.create_worktree(1)
    two = await manager.create_worktree(2)

    assert one.branch == dev_branch(RUN, 1) == "wiab/run-1-dev-1"
    assert one.path != two.path
    assert (one.path / "README.md").exists()

    # Writing in one worktree must not disturb the other.
    (one.path / "a.txt").write_text("from dev 1\n")
    assert not (two.path / "a.txt").exists()


async def test_commit_all_returns_none_when_nothing_changed(tmp_path: Path) -> None:
    manager = await _manager(tmp_path)
    worktree = await manager.create_worktree(1)
    assert await manager.commit_all(worktree, "no-op") is None


async def test_commit_and_merge_lands_on_the_work_branch(tmp_path: Path) -> None:
    manager = await _manager(tmp_path)
    worktree = await manager.create_worktree(1)
    (worktree.path / "feature.txt").write_text("hello\n")

    commit = await manager.commit_all(worktree, "Add feature")
    assert commit is not None and commit.message == "Add feature"

    outcome = await manager.merge_into_work(worktree.branch)
    assert outcome.merged
    assert (manager.repo_path / "feature.txt").read_text() == "hello\n"

    assert [c.message for c in await manager.commits_on(worktree.branch)] == []
    assert await manager.files_changed(worktree.branch) == []


async def test_reporting_helpers_see_work_before_it_is_merged(tmp_path: Path) -> None:
    manager = await _manager(tmp_path)
    worktree = await manager.create_worktree(1)
    (worktree.path / "feature.txt").write_text("hello\n")
    await manager.commit_all(worktree, "Add feature")

    assert [c.message for c in await manager.commits_on(worktree.branch)] == ["Add feature"]
    assert await manager.files_changed(worktree.branch) == ["feature.txt"]


async def test_conflicting_merge_aborts_and_reports_the_paths(tmp_path: Path) -> None:
    """The work branch must survive a conflict untouched, so the run can continue."""
    manager = await _manager(tmp_path, files={"shared.txt": "base\n"})
    one = await manager.create_worktree(1)
    two = await manager.create_worktree(2)

    (one.path / "shared.txt").write_text("from dev 1\n")
    await manager.commit_all(one, "dev 1 edits shared")
    (two.path / "shared.txt").write_text("from dev 2\n")
    await manager.commit_all(two, "dev 2 edits shared")

    assert (await manager.merge_into_work(one.branch)).merged

    outcome = await manager.merge_into_work(two.branch)
    assert not outcome.merged
    assert outcome.conflicted_paths == ("shared.txt",)

    # Aborted cleanly: the first dev's content still stands and the tree is clean.
    assert (manager.repo_path / "shared.txt").read_text() == "from dev 1\n"
    status = await run_git("status", "--porcelain", cwd=manager.repo_path)
    assert status.stdout.strip() == ""


async def test_a_clean_rebase_picks_up_the_other_devs_work(tmp_path: Path) -> None:
    manager = await _manager(tmp_path, files={"shared.txt": "base\n"})
    one = await manager.create_worktree(1)
    two = await manager.create_worktree(2)

    (one.path / "other.txt").write_text("dev 1\n")
    await manager.commit_all(one, "dev 1")
    await manager.merge_into_work(one.branch)

    (two.path / "shared.txt").write_text("dev 2\n")
    await manager.commit_all(two, "dev 2")

    assert await manager.begin_rebase(two) == ()
    assert not await manager.rebase_in_progress(two)
    assert (two.path / "other.txt").exists()  # picked up dev 1's work
    assert (await manager.merge_into_work(two.branch)).merged


async def _conflicted_pair(tmp_path: Path) -> tuple[WorktreeManager, Worktree]:
    """Two devs edit the same line; dev 1 is merged, dev 2 is left mid-rebase."""
    manager = await _manager(tmp_path, files={"shared.txt": "base\n"})
    one = await manager.create_worktree(1)
    two = await manager.create_worktree(2)

    (one.path / "shared.txt").write_text("dev 1\n")
    await manager.commit_all(one, "dev 1")
    await manager.merge_into_work(one.branch)

    (two.path / "shared.txt").write_text("dev 2\n")
    await manager.commit_all(two, "dev 2")
    return (manager, two)


async def test_a_conflicting_rebase_stays_open_with_markers(tmp_path: Path) -> None:
    """The agent needs to see both sides, so the rebase must not be aborted."""
    manager, two = await _conflicted_pair(tmp_path)

    assert await manager.begin_rebase(two) == ("shared.txt",)
    assert await manager.rebase_in_progress(two)
    assert "<<<<<<<" in (two.path / "shared.txt").read_text()


async def test_continue_rebase_lands_a_resolved_conflict(tmp_path: Path) -> None:
    manager, two = await _conflicted_pair(tmp_path)
    await manager.begin_rebase(two)

    (two.path / "shared.txt").write_text("dev 1\ndev 2\n")

    assert await manager.continue_rebase(two) is True
    assert not await manager.rebase_in_progress(two)
    assert (await manager.merge_into_work(two.branch)).merged
    assert (manager.repo_path / "shared.txt").read_text() == "dev 1\ndev 2\n"


async def test_continue_rebase_refuses_to_commit_conflict_markers(tmp_path: Path) -> None:
    """Markers reaching the work branch is the one outcome that must be impossible."""
    manager, two = await _conflicted_pair(tmp_path)
    await manager.begin_rebase(two)
    # The agent gives up and leaves the file exactly as git wrote it.

    assert await manager.continue_rebase(two) is False
    assert not await manager.rebase_in_progress(two)
    status = await run_git("status", "--porcelain", cwd=two.path)
    assert status.stdout.strip() == ""
    assert "<<<<<<<" not in (manager.repo_path / "shared.txt").read_text()


async def test_push_work_branch_reaches_the_origin(tmp_path: Path) -> None:
    origin = await make_origin(tmp_path)
    manager = WorktreeManager(
        workspace=tmp_path / "ws", run_id=RUN, remote=str(origin), base_branch="main"
    )
    await manager.clone()
    worktree = await manager.create_worktree(1)
    (worktree.path / "f.txt").write_text("x\n")
    await manager.commit_all(worktree, "work")
    await manager.merge_into_work(worktree.branch)

    await manager.push_work_branch()

    branches = await run_git("branch", "--list", cwd=origin)
    assert work_branch(RUN) in branches.stdout


async def test_cleanup_removes_worktrees_and_never_raises(tmp_path: Path) -> None:
    manager = await _manager(tmp_path)
    worktree = await manager.create_worktree(1)
    await manager.cleanup()
    assert not worktree.path.exists()
    await manager.cleanup()  # idempotent


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://user:tok@host/repos/R-3.git", "https://host/repos/R-3.git"),
        ("https://host/repos/R-3.git", "https://host/repos/R-3.git"),
        ("/tmp/local.git", "/tmp/local.git"),
        ("ssh://host:2222/R-3.git", "ssh://host:2222/R-3.git"),
    ],
)
def test_redact_strips_credentials(url: str, expected: str) -> None:
    assert redact(url) == expected


def test_authenticated_remote_only_touches_bare_https_urls() -> None:
    assert (
        authenticated_remote("https://host/r.git", "tok") == "https://x-access-token:tok@host/r.git"
    )
    assert authenticated_remote("https://host/r.git", None) == "https://host/r.git"
    assert authenticated_remote("/tmp/local.git", "tok") == "/tmp/local.git"
    assert authenticated_remote("ssh://host/r.git", "tok") == "ssh://host/r.git"
    already = "https://u:p@host/r.git"
    assert authenticated_remote(already, "tok") == already
