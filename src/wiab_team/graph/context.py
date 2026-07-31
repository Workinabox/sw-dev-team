"""Everything a node needs that is not serializable state.

Held outside ``TeamState`` so the checkpointer only ever persists plain data, and
so every node is testable by handing it a different context.
"""

from __future__ import annotations

from dataclasses import dataclass

from wiab_team.config import Config
from wiab_team.delivery.protocol import DeliveryStrategy
from wiab_team.tools.protocol import ToolProvider
from wiab_team.vcs.worktrees import WorktreeManager


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    config: Config
    provider: ToolProvider
    worktrees: WorktreeManager
    delivery: DeliveryStrategy
