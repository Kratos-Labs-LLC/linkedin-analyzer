"""Tiny retry helper for Anthropic calls.

Catches the transient classes (rate limit, server error, network blip) and
retries with exponential backoff. Surfaces every other error unchanged so
caller-side logic (parse failures, auth errors) keeps working as designed.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

import anthropic

log = logging.getLogger(__name__)

# Anthropic SDK exception types worth retrying. APIConnectionError covers
# DNS / connection-reset blips; APIStatusError catches everything else and
# we then introspect the status code.
_TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 1.5  # seconds, doubled per attempt

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    description: str = "anthropic call",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `fn()` with retries on transient Anthropic errors.

    `sleep` is injectable so tests can avoid real waits.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except anthropic.APIConnectionError as e:
            last_exc = e
            log.warning(
                "%s: connection error on attempt %d/%d (%s)",
                description,
                attempt,
                max_attempts,
                e,
            )
        except anthropic.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status not in _TRANSIENT_STATUSES:
                raise
            last_exc = e
            log.warning(
                "%s: HTTP %s on attempt %d/%d",
                description,
                status,
                attempt,
                max_attempts,
            )

        if attempt < max_attempts:
            sleep(base_delay * (2 ** (attempt - 1)))

    assert last_exc is not None  # we only reach here after a transient
    raise last_exc
