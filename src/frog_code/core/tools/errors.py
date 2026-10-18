from __future__ import annotations


class FrogRateLimitedError(Exception):
    """Raised by a tool when the upstream service is rate-limiting the request."""
