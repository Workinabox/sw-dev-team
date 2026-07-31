"""How a finished work branch reaches the outside world."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from wiab_team.models.input import TeamRunInput
from wiab_team.models.result import DeliveryReport
from wiab_team.vcs.worktrees import WorktreeManager


@runtime_checkable
class DeliveryStrategy(Protocol):
    """Publishes the work branch.

    Implementations never raise for an expected failure — a rejected push or a
    forge error becomes a ``DeliveryReport`` carrying ``error``, so the run still
    produces a complete result document describing what happened.
    """

    async def deliver(
        self,
        *,
        payload: TeamRunInput,
        worktrees: WorktreeManager,
        title: str,
        body: str,
    ) -> DeliveryReport: ...
