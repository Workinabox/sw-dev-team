"""A provider that runs no model.

Two uses: deterministic tests of the whole graph, and `--provider stub` for
exercising a real end-to-end run (clone, worktrees, commits, merge, delivery)
without spending tokens.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from wiab_team.tools.protocol import AgentRequest, AgentResult

Handler = Callable[[AgentRequest], Awaitable[AgentResult] | AgentResult]


class ScriptedProvider:
    """Dispatches to a per-role handler, falling back to a default behaviour.

    Records every request in ``calls`` so tests can assert on what the graph asked
    for, not just on what came back.
    """

    def __init__(self, handlers: dict[str, Handler] | None = None) -> None:
        self._handlers = handlers or {}
        self.calls: list[AgentRequest] = []

    async def run(self, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        handler = self._handlers.get(request.role)
        if handler is None:
            return self._default(request)
        outcome = handler(request)
        if isinstance(outcome, AgentResult):
            return outcome
        return await outcome

    async def aclose(self) -> None:
        return None

    def calls_for(self, role: str) -> list[AgentRequest]:
        return [c for c in self.calls if c.role == role]

    def _default(self, request: AgentRequest) -> AgentResult:
        """Behave plausibly enough that the graph's real machinery gets exercised."""
        if request.role == "architect":
            return AgentResult(text=json.dumps(_default_plan()))
        if request.role == "dev":
            # Write something, so there is a genuine commit to merge.
            marker = request.workdir / f"{request.workdir.name}.txt"
            marker.write_text(f"work by {request.workdir.name}\n")
            return AgentResult(text=f"wrote {marker.name}")
        if request.role == "tester":
            # The verdict line the tester prompt asks for; without it the run
            # would enter the repair loop and `--provider stub` would never
            # demonstrate the happy path.
            return AgentResult(text="Nothing to run.\nVERDICT: PASS")
        return AgentResult(text="ok")


def _default_plan() -> dict[str, object]:
    return {
        "summary": "stub plan",
        "work_items": [
            {"id": "w1", "title": "First slice", "instruction": "do the first slice"},
            {"id": "w2", "title": "Second slice", "instruction": "do the second slice"},
        ],
        "tester": {"can_start_early": False, "scaffold_instruction": ""},
        "test_command": "",
    }


def writes_file(name: str, content: str, *, text: str = "done") -> Handler:
    """Build a handler that writes one file into the agent's working directory."""

    def handler(request: AgentRequest) -> AgentResult:
        target = Path(request.workdir) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return AgentResult(text=text)

    return handler


def fails(error: str) -> Handler:
    """Build a handler that reports an agent-level failure (without raising)."""

    def handler(_: AgentRequest) -> AgentResult:
        return AgentResult(text="", succeeded=False, error=error)

    return handler
