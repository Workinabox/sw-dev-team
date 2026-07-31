"""The whole graph, driven by a scripted provider against a real git repo.

No API key, no network. These are the tests that prove the machinery — clone,
worktrees, concurrent devs, merge, conflict retry, repair loop, delivery — works
end to end before a model is ever involved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.git_repo import make_origin
from wiab_team.api import run_team
from wiab_team.config import Config, RoleConfig, ToolProviderKind
from wiab_team.delivery.local import LocalDelivery
from wiab_team.models.input import RepoRef, TaskSpec, TeamRunInput
from wiab_team.models.result import RunStatus
from wiab_team.tools.protocol import AgentRequest, AgentResult
from wiab_team.tools.stub import ScriptedProvider
from wiab_team.vcs.git import run_git

RUN = "run-e2e"


def make_config(workspace: Path, **overrides: Any) -> Config:
    base: dict[str, Any] = {
        "api_key": None,
        "tool_provider": ToolProviderKind.STUB,
        "workspace": workspace,
        "roles": {
            role: RoleConfig(model="claude-opus-4-8", effort=None)
            for role in ("architect", "dev", "tester")
        },
        "dev_count": 3,
        "max_repair_rounds": 1,
        "max_conflict_rounds": 1,
        "checkpoint_dsn": None,
        "log_level": "INFO",
        "log_json": True,
        "git_token": None,
    }
    base.update(overrides)
    return Config(**base)


def plan_json(
    *,
    items: list[dict[str, object]],
    can_start_early: bool = False,
    test_command: str = "",
) -> str:
    return json.dumps(
        {
            "summary": "test plan",
            "work_items": items,
            "tester": {
                "can_start_early": can_start_early,
                "scaffold_instruction": "write tests" if can_start_early else "",
            },
            "test_command": test_command,
        }
    )


def payload(origin: Path, **overrides: Any) -> TeamRunInput:
    data: dict[str, Any] = {
        "run_id": RUN,
        "task": TaskSpec(title="Add a feature", acceptance_criteria=["it works"]),
        "repo": RepoRef(remote=str(origin), base_branch="main"),
    }
    data.update(overrides)
    return TeamRunInput(**data)


def dev_writes_own_file(request: AgentRequest) -> AgentResult:
    """Each dev writes a distinct file, so their branches merge cleanly."""
    target = request.workdir / f"{request.workdir.name}.txt"
    target.write_text(f"content from {request.workdir.name}\n")
    return AgentResult(text=f"wrote {target.name}", input_tokens=10, output_tokens=5)


async def test_three_devs_land_a_merged_branch(tmp_path: Path) -> None:
    origin = await make_origin(tmp_path)
    workspace = tmp_path / "ws"
    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(
                    items=[
                        {"id": "w1", "title": "One", "instruction": "do one"},
                        {"id": "w2", "title": "Two", "instruction": "do two"},
                        {"id": "w3", "title": "Three", "instruction": "do three"},
                    ]
                ),
                input_tokens=100,
                output_tokens=50,
            ),
            "dev": dev_writes_own_file,
            "tester": lambda _: AgentResult(text="VERDICT: PASS"),
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.work_branch == "wiab/run-e2e"
    assert len(result.dev_results) == 3
    assert result.integration is not None
    assert len(result.integration.merged_branches) == 3
    assert result.integration.conflicts == []

    # Every dev's work is actually on the branch, in the real repository.
    repo = workspace / "repo"
    for index in (1, 2, 3):
        assert (repo / f"dev-{index}.txt").exists()

    # Three devs ran concurrently and their token usage was summed, not clobbered.
    assert result.token_usage["dev"].input_tokens == 30
    assert result.token_usage["architect"].input_tokens == 100


async def test_result_and_report_are_written(tmp_path: Path) -> None:
    origin = await make_origin(tmp_path)
    workspace = tmp_path / "ws"
    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(items=[{"id": "w1", "title": "One", "instruction": "do one"}])
            ),
            "dev": dev_writes_own_file,
            "tester": lambda _: AgentResult(text="VERDICT: PASS"),
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    written = json.loads((workspace / "result.json").read_text())
    assert written["schema_version"] == 1
    assert written["run_id"] == RUN
    assert written["status"] == result.status.value

    report = (workspace / "report.md").read_text()
    assert "run-e2e" in report
    assert "wiab/run-e2e" in report


async def test_tester_scaffolding_runs_alongside_the_devs(tmp_path: Path) -> None:
    origin = await make_origin(tmp_path)
    workspace = tmp_path / "ws"

    def tester(request: AgentRequest) -> AgentResult:
        # Only the scaffold pass runs in a worktree; the final pass runs in the clone.
        if "worktrees" in str(request.workdir):
            (request.workdir / "test_feature.py").write_text("def test_it(): assert True\n")
            return AgentResult(text="scaffolded")
        return AgentResult(text="VERDICT: PASS")

    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(
                    items=[{"id": "w1", "title": "One", "instruction": "do one"}],
                    can_start_early=True,
                )
            ),
            "dev": dev_writes_own_file,
            "tester": tester,
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert (workspace / "repo" / "test_feature.py").exists()
    assert any(r.work_item_id == "tester-scaffold" for r in result.dev_results)


async def test_conflicting_devs_are_sent_back_and_the_retry_lands(tmp_path: Path) -> None:
    """The conflict loop must resolve, not just record."""
    origin = await make_origin(tmp_path, files={"shared.txt": "base\n"})
    workspace = tmp_path / "ws"
    attempts: dict[str, int] = {}

    def dev(request: AgentRequest) -> AgentResult:
        name = request.workdir.name
        attempts[name] = attempts.get(name, 0) + 1
        shared = request.workdir / "shared.txt"
        if name != "dev-2":
            shared.write_text("dev 1 line\n")
        elif attempts[name] == 1:
            # First pass: collide head-on with dev-1 on the same line.
            shared.write_text("dev 2 line\n")
        else:
            # Retry: the rebase left conflict markers in place. Behave like an
            # agent that reconciles both sides and removes them.
            assert "<<<<<<<" in shared.read_text(), "the retry should see the conflict"
            shared.write_text("dev 1 line\ndev 2 line\n")
        return AgentResult(text=f"{name} edited shared.txt")

    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(
                    items=[
                        {"id": "w1", "title": "One", "instruction": "do one"},
                        {"id": "w2", "title": "Two", "instruction": "do two"},
                    ]
                )
            ),
            "dev": dev,
            "tester": lambda _: AgentResult(text="VERDICT: PASS"),
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    assert attempts["dev-2"] == 2, "the conflicted dev should have been retried"
    assert result.integration is not None
    assert result.integration.conflicts == []
    assert result.status is RunStatus.SUCCEEDED
    merged = (workspace / "repo" / "shared.txt").read_text()
    assert merged == "dev 1 line\ndev 2 line\n", "both developers' work must survive"


async def test_a_dev_that_cannot_resolve_is_reported_not_looped(tmp_path: Path) -> None:
    """An unresolved conflict ends the run as partial, never with markers merged."""
    origin = await make_origin(tmp_path, files={"shared.txt": "base\n"})
    workspace = tmp_path / "ws"

    def dev(request: AgentRequest) -> AgentResult:
        shared = request.workdir / "shared.txt"
        if "<<<<<<<" in shared.read_text():
            # An agent that gives up: it leaves the markers exactly as found.
            return AgentResult(text="I cannot reconcile these changes.")
        shared.write_text(f"{request.workdir.name} line\n")
        return AgentResult(text="edited shared.txt")

    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(
                    items=[
                        {"id": "w1", "title": "One", "instruction": "do one"},
                        {"id": "w2", "title": "Two", "instruction": "do two"},
                    ]
                )
            ),
            "dev": dev,
            "tester": lambda _: AgentResult(text="VERDICT: PASS"),
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace, max_conflict_rounds=1),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    assert result.status is RunStatus.PARTIAL
    assert result.integration is not None
    assert result.integration.merged_branches, "the other dev's work still landed"

    failed = [r for r in result.dev_results if not r.succeeded]
    assert len(failed) == 1
    assert "could not resolve" in (failed[0].error or "")

    # The one thing that must never happen: markers reaching the work branch.
    assert "<<<<<<<" not in (workspace / "repo" / "shared.txt").read_text()


async def test_failing_tests_trigger_one_repair_round_then_stop(tmp_path: Path) -> None:
    origin = await make_origin(tmp_path)
    workspace = tmp_path / "ws"
    dev_calls = {"n": 0}

    def dev(request: AgentRequest) -> AgentResult:
        dev_calls["n"] += 1
        (request.workdir / "impl.txt").write_text(f"attempt {dev_calls['n']}\n")
        return AgentResult(text="wrote impl.txt")

    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(
                    items=[{"id": "w1", "title": "One", "instruction": "do one"}],
                    # A command that always fails, so the repair loop always triggers.
                    test_command="false",
                )
            ),
            "dev": dev,
            "tester": lambda _: AgentResult(text="VERDICT: FAIL"),
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace, max_repair_rounds=1),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    # One initial pass plus exactly one repair — bounded, not infinite.
    assert dev_calls["n"] == 2
    assert result.status is RunStatus.PARTIAL
    assert result.test_reports and not result.test_reports[-1].passed


async def test_a_repair_round_does_not_erase_what_already_merged(tmp_path: Path) -> None:
    """Regression: a retry that merges nothing must not wipe the integration record.

    The second round produces no new commits, so it merges nothing. If integration
    were overwritten rather than accumulated, the run would report `failed` even
    though the work is sitting on the branch.
    """
    origin = await make_origin(tmp_path)
    workspace = tmp_path / "ws"
    calls = {"n": 0}

    def tester(_: AgentRequest) -> AgentResult:
        calls["n"] += 1
        # Fail the first verification, pass the second.
        return AgentResult(text="VERDICT: FAIL" if calls["n"] == 1 else "VERDICT: PASS")

    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(items=[{"id": "w1", "title": "One", "instruction": "do one"}])
            ),
            # Writes the same content every time, so the retry adds no commit.
            "dev": dev_writes_own_file,
            "tester": tester,
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace, max_repair_rounds=2),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    assert result.integration is not None
    assert result.integration.merged_branches == ["wiab/run-e2e-dev-1"]
    assert result.status is RunStatus.SUCCEEDED
    assert (workspace / "repo" / "dev-1.txt").exists()


async def test_an_architect_that_produces_no_plan_fails_cleanly(tmp_path: Path) -> None:
    origin = await make_origin(tmp_path)
    workspace = tmp_path / "ws"
    provider = ScriptedProvider({"architect": lambda _: AgentResult(text="I could not do this.")})

    result = await run_team(
        payload(origin),
        config=make_config(workspace),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert provider.calls_for("dev") == [], "no dev should run without a plan"
    # A failed run still produces a complete, parseable result document.
    assert json.loads((workspace / "result.json").read_text())["status"] == "failed"


async def test_a_failing_dev_does_not_sink_the_others(tmp_path: Path) -> None:
    origin = await make_origin(tmp_path)
    workspace = tmp_path / "ws"

    def dev(request: AgentRequest) -> AgentResult:
        if request.workdir.name == "dev-2":
            return AgentResult(text="", succeeded=False, error="ran out of ideas")
        return dev_writes_own_file(request)

    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(
                    items=[
                        {"id": "w1", "title": "One", "instruction": "do one"},
                        {"id": "w2", "title": "Two", "instruction": "do two"},
                    ]
                )
            ),
            "dev": dev,
            "tester": lambda _: AgentResult(text="VERDICT: PASS"),
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace),
        provider=provider,
        delivery=LocalDelivery(),
        workspace=workspace,
    )

    assert result.status is RunStatus.PARTIAL
    assert (workspace / "repo" / "dev-1.txt").exists(), "the healthy dev's work must land"
    failed = [r for r in result.dev_results if not r.succeeded]
    assert len(failed) == 1 and failed[0].error == "ran out of ideas"


async def test_the_work_branch_reaches_the_origin_on_push_delivery(tmp_path: Path) -> None:
    from wiab_team.delivery.push import PushDelivery

    origin = await make_origin(tmp_path)
    workspace = tmp_path / "ws"
    provider = ScriptedProvider(
        {
            "architect": lambda _: AgentResult(
                text=plan_json(items=[{"id": "w1", "title": "One", "instruction": "do one"}])
            ),
            "dev": dev_writes_own_file,
            "tester": lambda _: AgentResult(text="VERDICT: PASS"),
        }
    )

    result = await run_team(
        payload(origin),
        config=make_config(workspace),
        provider=provider,
        delivery=PushDelivery(),
        workspace=workspace,
    )

    assert result.delivery is not None and result.delivery.pushed
    branches = await run_git("branch", "--list", cwd=origin)
    assert "wiab/run-e2e" in branches.stdout


@pytest.mark.parametrize("max_devs", [1, 2])
def test_the_dev_cap_is_the_operators_not_the_architects(max_devs: int) -> None:
    """An architect that plans past the cap gets truncated, not obeyed."""
    from wiab_team.graph.nodes.architect import _parse_plan

    plan = _parse_plan(
        plan_json(
            items=[{"id": f"w{i}", "title": f"T{i}", "instruction": f"do {i}"} for i in range(1, 5)]
        ),
        max_devs=max_devs,
    )
    assert plan is not None
    assert len(plan.work_items) == max_devs
