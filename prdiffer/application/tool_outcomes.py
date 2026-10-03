"""Record outcomes at the public tool boundary, after client error translation."""

import time
from collections.abc import Awaitable, Callable
from functools import wraps

from prdiffer.domain.interfaces.protocols import MetricsTrackerProtocol


def record_outcome[**P, R](
    tracker: MetricsTrackerProtocol,
    operation: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Count ordinary completion exactly once; cancellation is not an outcome."""
    def decorate(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(function)
        async def recorded(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.time()
            try:
                result = await function(*args, **kwargs)
            except Exception:
                tracker.track_request(operation, False, time.time() - started)
                raise
            tracker.track_request(operation, True, time.time() - started)
            return result

        return recorded

    return decorate
