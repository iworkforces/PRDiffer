"""Unified retry handler implementation."""

import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import anyio

from prdiffer.infrastructure.utils.retry.base import BaseUnifiedRetryHandler
from prdiffer.infrastructure.utils.retry.models import (
    OperationContext,
    RETRY_EXCEPTIONS,
)
from prdiffer.infrastructure.utils.retry_logger import log_retry_attempt
from prdiffer.infrastructure.utils.error_classifier import (
    is_secondary_rate_limit_error,
)
from prdiffer.infrastructure.utils.rate_limit_parser import (
    extract_rate_limit_info,
)
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.domain.error_codes import E5001_INTERNAL_ERROR
from typing import cast


T = TypeVar("T")


class UnifiedRetryHandler(BaseUnifiedRetryHandler):
    """Unified retry handler supporting both sync and async operations."""

    def _execute_and_sleep(
        self,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        delay: float,
    ) -> Any:
        result = func(*args, **kwargs)

        if delay > 0:
            time.sleep(delay)

        return result

    def execute_with_retry(
        self,
        func: Callable[..., T],
        *args: Any,
        context: OperationContext | None = None,
        **kwargs: Any,
    ) -> T:
        return self._execute_with_retry_base(func, args, kwargs, context)

    async def execute_with_retry_async(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        context: OperationContext | None = None,
        **kwargs: Any,
    ) -> T:
        if self._circuit_breaker and self.circuit_breaker_enabled:
            if not self._circuit_breaker.can_execute():
                from prdiffer.infrastructure.utils.circuit_breaker_core import (
                    CircuitBreakerOpenException,
                )

                raise CircuitBreakerOpenException(f"Circuit breaker is open. State: {self._circuit_breaker.state.value}")

        config = self._get_context_config(context)
        max_retries = config["max_retries"]
        base_delay = config["retry_delay"]
        backoff_multiplier = config.get("backoff_multiplier", 2.0)

        last_exception: Exception | None = None
        start_time = time.time() if self._health_tracker else None

        for attempt in range(max_retries):
            try:
                result = await func(*args, **kwargs)

                if self._health_tracker and start_time:
                    self._record_success(start_time)

                return result

            except RETRY_EXCEPTIONS as e:
                exc = cast(Exception, e)
                last_exception = exc

                self._record_failure(exc)

                should_retry = self._should_retry_error(exc, context)
                is_last_attempt = attempt == max_retries - 1

                if not should_retry or is_last_attempt:
                    self._log_permanent_failure(exc, should_retry, is_last_attempt)
                    raise

                rate_limit_info = extract_rate_limit_info(exc)
                is_secondary_rate_limit = is_secondary_rate_limit_error(exc)
                delay = self._calculate_retry_delay(
                    attempt,
                    exc,
                    base_delay,
                    backoff_multiplier,
                    use_adaptive=self.adaptive_retry_enabled,
                    rate_limit_info=rate_limit_info,
                    is_secondary_rate_limit=is_secondary_rate_limit,
                )

                log_retry_attempt(
                    self._logger,
                    attempt,
                    delay,
                    exc,
                    self.retry_log_level,
                    context.value if context else None,
                    rate_limit_info=rate_limit_info,
                    is_secondary_rate_limit=is_secondary_rate_limit,
                    is_rate_limit_checker=self._is_rate_limit_error,
                )

                await anyio.sleep(delay)

        if last_exception:
            raise last_exception

        raise PRDifferException(
            "Unexpected state: no result and no exception",
            error_code=E5001_INTERNAL_ERROR,
        )
