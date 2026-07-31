"""The tester: an optional early preparation pass, and the final verification.

The tester owns *integration* testing — the pieces working together against the
acceptance criteria. Unit tests belong to the developers, who write their own for
what they build.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from wiab_team.graph.context import RuntimeContext
from wiab_team.graph.nodes.architect import TESTER_INDEX
from wiab_team.graph.nodes.integrate import latest_dev_results
from wiab_team.graph.prompts import load
from wiab_team.graph.state import TeamState
from wiab_team.logging import get_logger
from wiab_team.models.result import DevResult, Event, TestReport, TokenUsage
from wiab_team.tools.protocol import AgentRequest

log = get_logger(__name__)

# Keep the result document small; the full output goes to the log stream.
OUTPUT_TAIL_CHARS = 4000
TEST_TIMEOUT_SECONDS = 900


async def run_test_command(command: str, cwd: Path) -> tuple[int, str]:
    """Run the suite. A timeout is a failure, not an exception that kills the run."""
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=TEST_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return (124, f"timed out after {TEST_TIMEOUT_SECONDS}s")
    return (process.returncode or 0, out.decode("utf-8", "replace"))


def make_tester_scaffold(ctx: RuntimeContext):  # type: ignore[no-untyped-def]
    """Prepare integration fixtures and harness while the developers build."""

    async def tester_scaffold(state: TeamState) -> TeamState:
        plan = state["plan"]
        payload = state["input"]
        role = ctx.config.role("tester")

        worktree = ctx.worktrees.worktrees.get(TESTER_INDEX)
        if worktree is None:  # pragma: no cover - only created when starting early
            return {"events": [Event(node="tester_scaffold", message="no worktree; skipped")]}

        prompt = load("tester_scaffold").format(
            task_title=payload.task.title,
            scaffold_instruction=plan.tester.scaffold_instruction,
            plan_summary=plan.summary,
        )
        result = await ctx.provider.run(
            AgentRequest(
                role="tester",
                prompt=prompt,
                workdir=worktree.path,
                model=role.model,
                effort=role.effort,
            )
        )
        usage = {
            "tester": TokenUsage(
                input_tokens=result.input_tokens, output_tokens=result.output_tokens
            )
        }
        if not result.succeeded:
            return {
                "token_usage": usage,
                "events": [Event(node="tester_scaffold", message=f"failed: {result.error}")],
            }

        commit = await ctx.worktrees.commit_all(worktree, "Add integration test scaffolding")
        # Surfaced as a dev result so integrate merges it like any other branch.
        return {
            "dev_results": [
                DevResult(
                    work_item_id="tester-scaffold",
                    branch=worktree.branch,
                    summary=result.text,
                    commits=[commit] if commit else [],
                    files_changed=await ctx.worktrees.files_changed(worktree.branch),
                    succeeded=True,
                )
            ],
            "token_usage": usage,
            "events": [
                Event(
                    node="tester_scaffold",
                    message="wrote test scaffolding" if commit else "no tests written",
                )
            ],
        }

    return tester_scaffold


def make_tester_final(ctx: RuntimeContext):  # type: ignore[no-untyped-def]
    """Verify the merged work branch."""

    async def tester_final(state: TeamState) -> TeamState:
        plan = state["plan"]
        payload = state["input"]
        role = ctx.config.role("tester")
        repo = ctx.worktrees.repo_path

        summaries = "\n".join(
            f"- {r.work_item_id}: {r.summary or 'no summary'}"
            for r in latest_dev_results(state.get("dev_results", []))
        )
        criteria = payload.task.acceptance_criteria
        prompt = load("tester_final").format(
            task_title=payload.task.title,
            acceptance_criteria="\n".join(f"- {c}" for c in criteria) or "None stated.",
            dev_summaries=summaries or "Nothing reported.",
            test_command=plan.test_command or "(the team did not identify one)",
        )

        result = await ctx.provider.run(
            AgentRequest(
                role="tester",
                prompt=prompt,
                workdir=repo,
                model=role.model,
                effort=role.effort,
            )
        )
        usage = {
            "tester": TokenUsage(
                input_tokens=result.input_tokens, output_tokens=result.output_tokens
            )
        }
        verdict = result.text.splitlines()[-1] if result.text else "no verdict"
        events = [Event(node="tester_final", message=verdict)]

        # Keep any tests the verification pass added.
        await _commit_work_branch(ctx, "Add tests from verification pass")

        # The command is the ground truth; the agent's verdict is the tie-breaker
        # when there is no command to run.
        exit_code: int | None
        if plan.test_command:
            exit_code, output = await run_test_command(plan.test_command, repo)
            passed = exit_code == 0
        else:
            exit_code, output = (None, result.text)
            passed = result.succeeded and "VERDICT: PASS" in result.text.upper()

        log.info("tested", passed=passed, exit_code=exit_code)
        return {
            "test_reports": [
                TestReport(
                    phase="final",
                    command=plan.test_command,
                    passed=passed,
                    exit_code=exit_code,
                    output_tail=output[-OUTPUT_TAIL_CHARS:],
                )
            ],
            "repair_round": state.get("repair_round", 0) + 1,
            "token_usage": usage,
            "events": events,
        }

    return tester_final


async def _commit_work_branch(ctx: RuntimeContext, message: str) -> None:
    """Commit anything the agent left in the main clone, if it changed something."""
    from wiab_team.vcs.git import run_git

    await run_git("add", "-A", cwd=ctx.worktrees.repo_path)
    status = await run_git("status", "--porcelain", cwd=ctx.worktrees.repo_path)
    if status.stdout.strip():
        await run_git("commit", "-m", message, cwd=ctx.worktrees.repo_path)
