"""Request coalescing service implementation."""

import anyio
import logging
from collections.abc import Callable, Awaitable
from typing import Any
from dataclasses import dataclass, field

from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.settings import get_settings_service
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.domain.error_codes import E5001_INTERNAL_ERROR


DEFAULT_MAX_WAITERS = 100


@dataclass
class CoalescedOwnerCancelledError(RuntimeError):
    """The shared owner was cancelled, independently of this waiter."""

    key: str

    def __str__(self) -> str:
        return f"Coalesced request owner cancelled for key '{self.key}'"


@dataclass
class CoalescedRequest:
    key: str
    event: anyio.Event = field(default_factory=anyio.Event)
    result: Any | None = None
    exception: BaseException | None = None
    request_count: int = 1


class RequestCoalescingService:
    """Coalesces duplicate concurrent requests so only one API call is made
    and all requesters receive the same result.
    """

    def __init__(self, logger: logging.Logger | None = None, max_waiters: int | None = None) -> None:
        self._pending_requests: dict[str, CoalescedRequest] = {}
        self._lock = anyio.Lock()
        self._logger = logger or get_logger()

        if max_waiters is None:
            settings_service = get_settings_service()
            max_waiters = settings_service.get("request_coalescing.max_waiters", DEFAULT_MAX_WAITERS)
        if max_waiters is None:
            max_waiters = DEFAULT_MAX_WAITERS

        self._max_waiters = int(max_waiters)
        self._max_pending_requests = 1000  # Maximum concurrent unique requests (DoS prevention)

    async def coalesce(
        self,
        key: str,
        fetch_func: Callable[[], Awaitable[Any]],
        timeout: float | None = 30.0,
    ) -> Any:
        """Coalesce requests for the same key with timeout protection.

        If a request for this key is already in progress, wait for its result.
        Otherwise, execute the fetch function and share the result with all waiters.
        """
        existing_request = None
        overflow_standalone = False
        async with self._lock:
            if key in self._pending_requests:
                pending = self._pending_requests[key]

                if pending.request_count >= self._max_waiters:
                    # Do not wait and do not replace the in-flight owner — run a
                    # standalone fetch that leaves the existing pending entry intact.
                    overflow_standalone = True
                    self._logger.warning(f"Maximum waiters ({self._max_waiters}) reached for key '{key}', executing standalone request without replacing owner")
                else:
                    pending.request_count += 1
                    existing_request = pending
                    self._logger.debug(f"Coalescing request for key '{key}' (total waiting: {pending.request_count})")

        if overflow_standalone:
            effective_timeout = timeout if timeout is not None else 30.0
            return await self._execute_standalone(fetch_func, key, effective_timeout)

        if existing_request is not None:
            effective_timeout = timeout if timeout is not None else 30.0
            return await self._wait_for_request(existing_request, key, effective_timeout)

        new_request: CoalescedRequest | None = None
        async with self._lock:
            if key in self._pending_requests:
                pending = self._pending_requests[key]

                if pending.request_count >= self._max_waiters:
                    overflow_standalone = True
                    self._logger.warning(f"Maximum waiters ({self._max_waiters}) reached for key '{key}', executing standalone request without replacing owner")
                else:
                    pending.request_count += 1
                    existing_request = pending

            if existing_request is None and not overflow_standalone:
                # Check if we've reached max pending requests limit (DoS prevention)
                if len(self._pending_requests) >= self._max_pending_requests:
                    self._logger.warning(f"Maximum pending requests ({self._max_pending_requests}) reached, evicting oldest request")
                    if self._pending_requests:
                        oldest_key = next(iter(self._pending_requests))
                        oldest_request = self._pending_requests[oldest_key]
                        self._settle_request(oldest_request, exception=TimeoutError("Evicted due to pending request limit"))
                        del self._pending_requests[oldest_key]
                        self._logger.info(f"Evicted pending request for key '{oldest_key}' to make room for new request")

                new_request = CoalescedRequest(key=key)
                self._pending_requests[key] = new_request
                self._logger.debug(f"Starting new request for key '{key}' (total pending: {len(self._pending_requests)})")

        if overflow_standalone:
            effective_timeout = timeout if timeout is not None else 30.0
            return await self._execute_standalone(fetch_func, key, effective_timeout)

        if existing_request is not None:
            effective_timeout = timeout if timeout is not None else 30.0
            return await self._wait_for_request(existing_request, key, effective_timeout)

        if new_request is None:
            raise PRDifferException(
                f"Internal error: Request for key '{key}' should be owned by this task "
                "but new_request is None. This indicates a logic error in request coalescing.",
                error_code=E5001_INTERNAL_ERROR,
            )

        effective_timeout = timeout if timeout is not None else 30.0
        return await self._execute_request(new_request, key, fetch_func, effective_timeout)

    async def _execute_standalone(
        self,
        fetch_func: Callable[[], Awaitable[Any]],
        key: str,
        timeout: float,
    ) -> Any:
        """Run fetch without registering as the pending owner for ``key``."""
        try:
            with anyio.fail_after(timeout):
                return await fetch_func()
        except TimeoutError:
            self._logger.error(f"Standalone coalesced overflow request timed out for key '{key}'")
            raise TimeoutError(f"Request timed out after {timeout} seconds") from None

    async def _wait_for_request(self, existing_request: CoalescedRequest, key: str, timeout: float) -> Any:
        try:
            with anyio.fail_after(timeout):
                await existing_request.event.wait()

            if existing_request.exception is not None:
                raise existing_request.exception

            self._logger.debug(f"Request coalesced for key '{key}' - returning cached result")
            return existing_request.result
        except TimeoutError:
            self._logger.error(f"Coalesced request timed out for key '{key}'")
            raise
        except RuntimeError, AttributeError, KeyError:
            self._logger.error(f"Coalesced request failed for key '{key}'")
            raise
        finally:
            # Level cancellation must not interrupt acquisition of the bookkeeping lock.
            with anyio.CancelScope(shield=True):
                await self._decrement_waiter(existing_request)

    def _settle_request(self, request: CoalescedRequest, *, result: Any = None, exception: BaseException | None = None) -> None:
        """Settle once while holding the service lock, including pending-cap eviction."""
        if request.event.is_set():
            return
        request.result = result
        request.exception = exception
        request.event.set()

    async def _publish_terminal(
        self,
        key: str,
        request: CoalescedRequest,
        *,
        result: Any = None,
        exception: BaseException | None = None,
    ) -> None:
        """Publish terminal result/exception, signal waiters, remove exact owner entry."""
        async with self._lock:
            self._settle_request(request, result=result, exception=exception)
            pending = self._pending_requests.get(key)
            if pending is request:
                del self._pending_requests[key]

    async def _execute_request(
        self,
        new_request: CoalescedRequest,
        key: str,
        fetch_func: Callable[[], Awaitable[Any]],
        timeout: float,
    ) -> Any:
        try:
            with anyio.fail_after(timeout):
                result = await fetch_func()

            await self._publish_terminal(key, new_request, result=result)
            self._logger.info(f"Request completed for key '{key}' (served {new_request.request_count} waiters)")
            return result

        except TimeoutError:
            exc = TimeoutError(f"Request timed out after {timeout} seconds")
            # Shield so timeout cleanup still runs if a parent cancel arrives mid-cleanup.
            with anyio.CancelScope(shield=True):
                await self._publish_terminal(key, new_request, exception=exc)
            self._logger.error(f"Request timed out for key '{key}' after {timeout} seconds")
            raise exc

        except Exception as e:
            with anyio.CancelScope(shield=True):
                await self._publish_terminal(key, new_request, exception=e)
            self._logger.error(f"Request failed for key '{key}': {e}")
            raise

        except anyio.get_cancelled_exc_class():
            # Cancellation belongs to the owner task, not independent joined callers.
            with anyio.CancelScope(shield=True):
                await self._publish_terminal(key, new_request, exception=CoalescedOwnerCancelledError(key))
                self._logger.info(f"Request owner cancelled for key '{key}'; waiters notified")
            raise

        except BaseException as e:
            with anyio.CancelScope(shield=True):
                await self._publish_terminal(key, new_request, exception=e)
            raise

    async def _decrement_waiter(self, request: CoalescedRequest) -> None:
        async with self._lock:
            request.request_count -= 1

    async def clear(self) -> None:
        async with self._lock:
            self._pending_requests.clear()
            self._logger.info("Cleared all pending requests")

    async def get_stats(self) -> dict[str, Any]:
        async with self._lock:
            pending_count = len(self._pending_requests)
            pending_keys = list(self._pending_requests.keys())
            total_waiters = sum(req.request_count for req in self._pending_requests.values())

        return {
            "pending_count": pending_count,
            "pending_keys": pending_keys,
            "total_waiters": total_waiters,
        }


_request_coalescing_service: RequestCoalescingService | None = None


def get_request_coalescing_service() -> RequestCoalescingService:
    global _request_coalescing_service
    if _request_coalescing_service is None:
        _request_coalescing_service = RequestCoalescingService()
    return _request_coalescing_service
