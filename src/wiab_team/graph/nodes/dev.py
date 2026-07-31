"""One developer working in its own worktree.

The same node runs concurrently for every work item, fanned out with ``Send``.
"""

from __future__ import annotations

from wiab_team.graph.context import RuntimeContext
from wiab_team.graph.prompts import load
from wiab_team.graph.state import DevTask, TeamState
from wiab_team.logging import get_logger
from wiab_team.models.result import DevResult, Event, TokenUsage
from wiab_team.tools.protocol import AgentRequest

log = get_logger(__name__)


def make_dev(ctx: RuntimeContext):  # type: ignore[no-untyped-def]
    async def dev(task: DevTask) -> TeamState:
        index = task["index"]
        item = task["work_item"]
        payload = task["input"]
        role = ctx.config.role("dev")

        worktree = ctx.worktrees.worktrees.get(index)
        if worktree is None:  # pragma: no cover - architect always creates these
            return _failure(index, "", item.id, "no worktree was prepared")

        retry_reason = task.get("retry_reason") or ""
        conflicted: tuple[str, ...] = ()
        if retry_reason:
            # Replay this dev's commits onto current work-branch state. If that
            # conflicts, the rebase is left open with markers in the files so the
            # agent can reconcile both sides rather than re-raising the conflict.
            conflicted = await ctx.worktrees.begin_rebase(worktree)
            if conflicted:
                retry_reason += (
                    "\n\nThese files now contain git conflict markers "
                    f"(`<<<<<<<`, `=======`, `>>>>>>>`): {', '.join(conflicted)}. "
                    "Edit each one so it keeps both your change and the other "
                    "developer's, and remove every marker."
                )

        prompt = load("dev").format(
            task_title=payload.task.title,
            instruction=item.instruction,
            paths=(
                "## Files you own\n\n" + "\n".join(f"- {p}" for p in item.paths)
                if item.paths
                else ""
            ),
            retry=(
                f"## This is a retry\n\n{retry_reason}\n\n"
                "Address this specifically. Your earlier work is still in place."
                if retry_reason
                else ""
            ),
        )

        result = await ctx.provider.run(
            AgentRequest(
                role="dev",
                prompt=prompt,
                workdir=worktree.path,
                model=role.model,
                effort=role.effort,
            )
        )
        usage = {
            "dev": TokenUsage(input_tokens=result.input_tokens, output_tokens=result.output_tokens)
        }

        if not result.succeeded:
            log.warning("dev_failed", index=index, error=result.error)
            return _failure(index, worktree.branch, item.id, result.error or "agent failed", usage)

        if await ctx.worktrees.rebase_in_progress(
            worktree
        ) and not await ctx.worktrees.continue_rebase(worktree):
            log.warning("conflict_unresolved", index=index, paths=list(conflicted))
            return _failure(
                index,
                worktree.branch,
                item.id,
                "could not resolve the merge conflict in "
                f"{', '.join(conflicted) or 'the conflicted files'}",
                usage,
            )

        await ctx.worktrees.commit_all(worktree, f"{item.title}\n\n{result.text}".strip())
        # Report every commit on the branch, not just the one just made: a retry
        # that only resolved a rebase adds no new commit but still has work to merge.
        commits = await ctx.worktrees.commits_on(worktree.branch)
        files = await ctx.worktrees.files_changed(worktree.branch)

        log.info("dev_done", index=index, commits=len(commits), files=len(files))
        return {
            "dev_results": [
                DevResult(
                    work_item_id=item.id,
                    branch=worktree.branch,
                    summary=result.text,
                    commits=commits,
                    files_changed=files,
                    succeeded=True,
                )
            ],
            "token_usage": usage,
            "events": [
                Event(
                    node=f"dev-{index}",
                    message=(
                        f"{item.id}: {len(files)} file(s) changed"
                        if commits
                        else f"{item.id}: no changes made"
                    ),
                )
            ],
        }

    return dev


def _failure(
    index: int,
    branch: str,
    work_item_id: str,
    error: str,
    usage: dict[str, TokenUsage] | None = None,
) -> TeamState:
    state: TeamState = {
        "dev_results": [
            DevResult(
                work_item_id=work_item_id,
                branch=branch,
                succeeded=False,
                error=error,
            )
        ],
        "events": [Event(node=f"dev-{index}", message=f"failed: {error}")],
    }
    if usage:
        state["token_usage"] = usage
    return state
