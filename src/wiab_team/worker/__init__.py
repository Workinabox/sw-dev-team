"""The long-lived side of a team: pulling issues from a board and running them.

``wiab_team.api.run_team`` still runs exactly one issue. This package is the
loop around it, plus the client that talks to the backend's board.
"""

from wiab_team.worker.backend import BackendClient, BackendError, ClaimedTask
from wiab_team.worker.loop import WorkerSettings, build_input, work

__all__ = [
    "BackendClient",
    "BackendError",
    "ClaimedTask",
    "WorkerSettings",
    "build_input",
    "work",
]
