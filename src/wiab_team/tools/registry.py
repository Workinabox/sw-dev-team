"""Chooses a ToolProvider from configuration."""

from __future__ import annotations

from wiab_team.config import Config, ToolProviderKind
from wiab_team.tools.protocol import ToolProvider


def build_provider(config: Config) -> ToolProvider:
    if config.tool_provider is ToolProviderKind.STUB:
        from wiab_team.tools.stub import ScriptedProvider

        return ScriptedProvider()

    from wiab_team.tools.claude_sdk import ClaudeAgentSdkProvider

    return ClaudeAgentSdkProvider()
