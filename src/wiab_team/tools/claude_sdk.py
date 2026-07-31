"""The default provider: the Claude Agent SDK.

The SDK is Claude Code packaged as a library — it brings the file, search, and
shell tools, the agent loop, and context management, so this module only has to
translate an ``AgentRequest`` into ``ClaudeAgentOptions`` and read the result back.

Note the SDK drives the Node ``claude`` CLI as a subprocess; it must be on PATH
(or ``WIAB_TEAM_CLAUDE_CLI`` set). That is why the sandbox image needs Node, not
just Python.
"""

from __future__ import annotations

import os
from typing import Any

from wiab_team.logging import get_logger
from wiab_team.tools.protocol import AgentRequest, AgentResult

log = get_logger(__name__)

# The agents edit code and run tests; they have no business reaching the network
# or spawning sub-agents of their own. Anything not listed here is unavailable.
DEFAULT_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

# Sensible ceiling so a stuck agent ends the turn rather than the run.
DEFAULT_MAX_TURNS = 60


class ClaudeAgentSdkProvider:
    """Runs each agent turn as a fresh Claude Agent SDK session.

    Sessions are deliberately not reused across nodes: each node's prompt carries
    the context it needs, and a fresh session keeps one agent's confusion from
    leaking into the next.
    """

    def __init__(
        self,
        *,
        allowed_tools: list[str] | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        cli_path: str | None = None,
    ) -> None:
        self._allowed_tools = allowed_tools or list(DEFAULT_TOOLS)
        self._max_turns = max_turns
        self._cli_path = cli_path or os.environ.get("WIAB_TEAM_CLAUDE_CLI")

    async def run(self, request: AgentRequest) -> AgentResult:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ClaudeSDKError,
            ResultMessage,
            TextBlock,
        )

        options_kwargs: dict[str, Any] = {
            "model": request.model,
            "cwd": str(request.workdir),
            "allowed_tools": list(self._allowed_tools),
            "max_turns": request.max_turns or self._max_turns,
            # The agent owns its worktree; prompting for every edit would deadlock
            # a run that has no human attached to answer.
            "permission_mode": "acceptEdits",
            # Don't inherit the host's CLAUDE.md or settings: a run must behave the
            # same in a sandbox as it does on a developer's laptop.
            "setting_sources": [],
        }
        if request.effort is not None:
            options_kwargs["effort"] = request.effort
        if request.system_prompt is not None:
            options_kwargs["system_prompt"] = request.system_prompt
        if self._cli_path is not None:
            options_kwargs["cli_path"] = self._cli_path

        options = ClaudeAgentOptions(**options_kwargs)

        text_parts: list[str] = []
        result_message: Any = None

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(request.prompt)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        text_parts.extend(
                            block.text for block in message.content if isinstance(block, TextBlock)
                        )
                    elif isinstance(message, ResultMessage):
                        result_message = message
        except ClaudeSDKError as exc:
            # A broken harness (missing CLI, transport failure) is not an agent
            # failure — but the graph should still get a result it can record.
            log.error("agent_harness_failed", role=request.role, error=str(exc))
            return AgentResult(text="", succeeded=False, error=f"{type(exc).__name__}: {exc}")

        text = "\n".join(part for part in text_parts if part).strip()
        if result_message is None:
            return AgentResult(text=text, succeeded=False, error="no result message from the SDK")

        input_tokens, output_tokens = _usage(result_message)
        if result_message.is_error:
            error = "; ".join(result_message.errors or []) or (
                result_message.stop_reason or "agent reported an error"
            )
            return AgentResult(
                text=text,
                succeeded=False,
                error=error,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        return AgentResult(
            text=text or (result_message.result or ""),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata={"stop_reason": result_message.stop_reason or ""},
        )

    async def aclose(self) -> None:
        return None


def _usage(result_message: Any) -> tuple[int, int]:
    """Pull (input, output) token counts off a ResultMessage.

    Tolerates a missing or unexpected usage block: token accounting is reporting,
    and must never be the reason a run fails.
    """
    usage = getattr(result_message, "usage", None)
    if not isinstance(usage, dict):
        return (0, 0)
    # Cached reads and writes are still input tokens billed to this run.
    input_tokens = (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    )
    return (input_tokens, int(usage.get("output_tokens", 0) or 0))
