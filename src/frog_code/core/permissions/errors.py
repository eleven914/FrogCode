from __future__ import annotations


class FrogPermissionDeniedError(Exception):
    """Raised when a tool call is denied by the permission manager."""
