"""The conditional edges, tested directly.

The integration tests exercise the common paths; these pin the budget-exhaustion
branches, which are what stop a run looping forever and are hard to reach through
the full graph.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration.test_graph_end_to_end_stub import make_config, payload
from wiab_team.graph.routing import after_integrate, after_tests
from wiab_team.graph.state import TeamState
from wiab_team.models.plan import Plan, WorkItem
from wiab_team.models.result import Conflict, IntegrationReport, TestReport


def _state(tmp_path: Path, **overrides: object) -> TeamState:
    plan = Plan(work_items=[WorkItem(id="w1", title="One", instruction="do one")])
    state: TeamState = {"input": payload(tmp_path / "origin.git"), "plan": plan}
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def test_a_clean_integration_goes_straight_to_the_tester(tmp_path: Path) -> None:
    route = after_integrate(make_config(tmp_path))
    state = _state(
        tmp_path,
        integration=IntegrationReport(work_branch="wiab/x", merged_branches=["b"]),
    )
    assert route(state) == "tester_final"


def test_a_conflict_within_budget_sends_the_owning_dev_back(tmp_path: Path) -> None:
    route = after_integrate(make_config(tmp_path, max_conflict_rounds=1))
    state = _state(
        tmp_path,
        conflict_round=1,
        integration=IntegrationReport(
            work_branch="wiab/x",
            conflicts=[Conflict(work_item_id="w1", branch="b", paths=["f.txt"])],
        ),
    )
    sends = route(state)
    assert isinstance(sends, list) and len(sends) == 1
    assert sends[0].node == "dev"
    assert "conflict" in sends[0].arg["retry_reason"].lower()


def test_a_conflict_past_the_budget_stops_retrying(tmp_path: Path) -> None:
    """Past the budget the conflict is recorded rather than retried forever."""
    route = after_integrate(make_config(tmp_path, max_conflict_rounds=1))
    state = _state(
        tmp_path,
        conflict_round=2,
        integration=IntegrationReport(
            work_branch="wiab/x",
            conflicts=[Conflict(work_item_id="w1", branch="b", paths=["f.txt"])],
        ),
    )
    assert route(state) == "tester_final"


def test_a_conflict_on_an_unknown_work_item_does_not_hang_the_run(tmp_path: Path) -> None:
    """A conflict the plan has no work item for cannot be routed; go on regardless."""
    route = after_integrate(make_config(tmp_path, max_conflict_rounds=1))
    state = _state(
        tmp_path,
        conflict_round=1,
        integration=IntegrationReport(
            work_branch="wiab/x",
            conflicts=[Conflict(work_item_id="ghost", branch="b")],
        ),
    )
    assert route(state) == "tester_final"


def test_passing_tests_go_to_delivery(tmp_path: Path) -> None:
    route = after_tests(make_config(tmp_path))
    state = _state(tmp_path, test_reports=[TestReport(phase="final", passed=True)])
    assert route(state) == "deliver"


def test_failing_tests_past_the_repair_budget_go_to_delivery(tmp_path: Path) -> None:
    """A partial result still gets delivered; the run must not loop."""
    route = after_tests(make_config(tmp_path, max_repair_rounds=1))
    state = _state(
        tmp_path,
        repair_round=2,
        test_reports=[TestReport(phase="final", passed=False)],
    )
    assert route(state) == "deliver"
