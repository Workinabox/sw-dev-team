"""How this package verifies the workinabox backend.

The backend generates a self-signed certificate unless one is configured, and
hands it to the team. Trusting exactly that beats disabling verification: a
team still refuses to talk to anything else.

Both the board client and the forge talk to the same server, so they resolve
verification the same way, here.
"""

from __future__ import annotations

from typing import Any


def verification_for(certificate_pem: str | None) -> Any:
    """What to pass to httpx's ``verify``: the system store, or exactly this cert."""
    if not certificate_pem:
        return True
    import ssl

    context = ssl.create_default_context()
    context.load_verify_locations(cadata=certificate_pem)
    return context
