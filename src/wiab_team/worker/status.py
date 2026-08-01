"""A small status endpoint, for looking at a team that seems stuck.

Deliberately a *debugging* surface, not a control path. Control is the board:
the backend pauses a team by changing its state, and the team asks. Nothing
here changes anything, so nothing here needs authentication beyond the fact
that the sandbox's network is not published.

Uses the standard library rather than a web framework. One read-only endpoint
does not justify a dependency, and this runs inside every team container.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from wiab_team.logging import get_logger

log = get_logger(__name__)


@dataclass
class WorkerStatus:
    """What the team is doing right now.

    Mutated from the worker loop and read from the server's thread, so every
    access takes the lock — the fields are small and the traffic is one curl.
    """

    team_id: str
    board_id: str
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _started: float = field(default_factory=time.monotonic)
    _state: str = "starting"
    _task_id: str | None = None
    _task_title: str | None = None
    _task_started: float | None = None
    _completed: int = 0
    _last_error: str | None = None

    def waiting(self, *, paused: bool = False) -> None:
        with self._lock:
            self._state = "paused" if paused else "waiting"
            self._task_id = None
            self._task_title = None
            self._task_started = None

    def working(self, task_id: str, title: str) -> None:
        with self._lock:
            self._state = "working"
            self._task_id = task_id
            self._task_title = title
            self._task_started = time.monotonic()

    def finished(self, outcome: str, error: str | None = None) -> None:
        with self._lock:
            if outcome == "completed":
                self._completed += 1
            self._last_error = error
            self._state = "waiting"
            self._task_id = None
            self._task_title = None
            self._task_started = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {
                "team_id": self.team_id,
                "board_id": self.board_id,
                "state": self._state,
                "uptime_seconds": round(now - self._started, 1),
                "task_id": self._task_id,
                "task_title": self._task_title,
                # The number that answers "is it stuck?" — a task in flight for
                # far longer than the others is the thing worth looking at.
                "task_seconds": (
                    round(now - self._task_started, 1) if self._task_started is not None else None
                ),
                "issues_completed": self._completed,
                "last_error": self._last_error,
            }


def serve(status: WorkerStatus, port: int) -> ThreadingHTTPServer:
    """Start the status server on a daemon thread and return it.

    Daemon so it never keeps the process alive: when the worker loop ends, the
    container should exit rather than linger serving a status nobody wants.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") in ("/health", ""):
                self._respond({"status": "ok"})
            elif self.path.rstrip("/") == "/status":
                self._respond(status.snapshot())
            else:
                self._respond({"error": "not found"}, code=404)

        def _respond(self, body: dict[str, Any], code: int = 200) -> None:
            payload = json.dumps(body, indent=2).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            """Silence the default stderr access log; it would drown the run's own."""

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("status_server_listening", port=port)
    return server
