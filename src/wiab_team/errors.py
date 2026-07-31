"""Error hierarchy. Everything raised deliberately by this package derives from TeamError."""

from __future__ import annotations


class TeamError(Exception):
    """Base class for all errors this package raises on purpose."""


class ConfigError(TeamError):
    """Environment is misconfigured. Raised at startup, before the graph runs."""


class GitError(TeamError):
    """A git command failed."""

    def __init__(self, command: str, exit_code: int, stderr: str) -> None:
        super().__init__(f"git {command} failed ({exit_code}): {stderr.strip()}")
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr


class ProviderError(TeamError):
    """The tool provider failed to complete a turn."""


class ForgeError(TeamError):
    """The forge rejected a request, or does not support the operation."""


class DeliveryError(TeamError):
    """Delivery could not complete."""
