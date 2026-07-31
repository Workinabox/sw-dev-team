"""The seam between the graph and whatever actually drives a model.

The graph nodes know only this protocol. That keeps the whole graph runnable in
tests with no API key, and lets a different agent harness be dropped in without
touching any node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """One unit of agent work: a prompt, a directory to work in, and model settings."""

    role: str
    prompt: str
    workdir: Path
    model: str
    effort: str | None = None
    # Upper bound on agent turns, so a confused agent can't spin forever.
    max_turns: int | None = None
    # System prompt appended to the harness default, not replacing it.
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class AgentResult:
    """What came back. ``text`` is the agent's final message."""

    text: str
    succeeded: bool = True
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # Anything the provider wants to surface for debugging; never load-bearing.
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ToolProvider(Protocol):
    """Runs one agent turn to completion in a working directory.

    Implementations are responsible for giving the agent file and shell access
    scoped to ``request.workdir``. A provider must not raise for an agent-level
    failure — it returns ``succeeded=False`` so the graph can decide what to do.
    Raising is reserved for the harness itself being broken.
    """

    async def run(self, request: AgentRequest) -> AgentResult: ...

    async def aclose(self) -> None: ...
