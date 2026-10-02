"""Base retry handler with common logic."""

import logging
import time
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from prdiffer.domain.services.retry import RetryServiceInterface
from prdiffer.infrastructure.utils.retry_logger import (
    log_retry_attempt,
    log_permanent_failure,
)
from prdiffer.infrastructure.utils.error_classifier import (
    categorize_error,
    is_secondary_rate_limit_error,
    is_rate_limit_error as is_rate_limit_error_classifier,
    classify_error_for_retry,
)
from prdiffer.infrastructure.utils.rate_limit_parser import (
    RateLimitInfo,
    extract_rate_limit_info,
)
from prdiffer.infrastructure.utils.delay_calculator import (
    calculate_retry_delay as calculate_retry_delay_impl,
)
from prdiffer.infrastructure.utils.retry.models import (
    OperationContext,
    RETRY_EXCEPTIONS,
)
from typing import cast


class BaseUnifiedRetryHandler(RetryServiceInterface):
    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        retry_on_404: bool = False,
        retry_on_403: bool = True,
        retry_on_500: bool = True,
        retry_log_level: str = "DEBUG",
        permanent_failure_log_level: str = "INFO",
        circuit_breaker_enabled: bool = False,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_timeout: float = 60.0,
        adaptive_retry_enabled: bool = False,
        max_adaptive_delay: float = 30.0,
        rate_limit_remaining_threshold: int = 1,
        rate_limit_reset_buffer: float = 1.0,
        secondary_rate_limit_backoff: float = 60.0,
        api_health_tracking: bool = False,
        context_aware_retry: bool = False,
        logger: logging.Logger | None = None,
    ):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.retry_on_404 = retry_on_404
        self.retry_on_403 = retry_on_403
        self.retry_on_500 = retry_on_500
        self.retry_log_level = retry_log_level.upper()
        self.permanent_failure_log_level = permanent_failure_log_level.upper()

        self._logger = logger or logging.getLogger(__name__)

        self.circuit_breaker_enabled = circuit_breaker_enabled
        self.adaptive_retry_enabled = adaptive_retry_enabled
        self.api_health_tracking = api_health_tracking
        self.context_aware_retry = context_aware_retry

        self.max_adaptive_delay = max_adaptive_delay
        self.rate_limit_remaining_threshold = rate_limit_remaining_threshold
        self.rate_limit_reset_buffer = rate_limit_reset_buffer
        self.secondary_rate_limit_backoff = secondary_rate_limit_backoff

        self._circuit_breaker: Any | None = None
        self._health_tracker: Any | None = None

        if self.circuit_breaker_enabled:
            from prdiffer.infrastructure.utils.circuit_breaker_core import (
                CircuitBreaker,
            )

            self._circuit_breaker = CircuitBreaker(
                failure_threshold=circuit_breaker_failure_threshold,
                timeout=circuit_breaker_timeout,
                logger=self._logger,
            )

        if self.api_health_tracking:
            from prdiffer.infrastructure.utils.api_health_tracker import (
                APIHealthTracker,
            )

            self._health_tracker = APIHealthTracker(logger=self._logger)

        self._context_configs: dict[OperationContext, dict[str, Any]] = {}
        if self.context_aware_retry:
            self._context_configs = {
                OperationContext.REPOSITORY_ACCESS: {
                    "max_retries": max_retries,
                    "retry_delay": retry_delay * 2,
                    "backoff_multiplier": 2.0,
                },
                OperationContext.FILE_CONTENT: {
                    "max_retries": max_retries - 1,
                    "retry_delay": retry_delay,
                    "backoff_multiplier": 1.5,
                },
                OperationContext.PULL_REQUEST: {
                    "max_retries": max_retries + 1,
                    "retry_delay": retry_delay,
                    "backoff_multiplier": 2.0,
                },
                OperationContext.BATCH_OPERATION: {
                    "max_retries": max_retries - 1,
                    "retry_delay": retry_delay * 0.5,
                    "backoff_multiplier": 1.5,
                },
            }

    @abstractmethod
    def _execute_and_sleep(
        self,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        delay: float,
    ) -> Any:
        pass

    def _execute_with_retry_base(
        self,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        context: OperationContext | None = None,
    ) -> Any:
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

        last_exception = None
        start_time = time.time() if self._health_tracker else None

        for attempt in range(max_retries):
            try:
                result = self._execute_and_sleep(func, args, kwargs, 0.0)

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
                    log_permanent_failure(
                        self._logger,
                        exc,
                        self.permanent_failure_log_level,
                        should_retry,
                        is_last_attempt,
                    )
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

                self._execute_and_sleep(lambda: None, (), {}, delay)

        if last_exception:
            raise last_exception

    def _get_context_config(self, context: OperationContext | None) -> dict[str, Any]:
        if context and self.context_aware_retry and context in self._context_configs:
            return self._context_configs[context]

        return {
            "max_retries": self.max_retries,
            "retry_delay": self.retry_delay,
            "backoff_multiplier": 2.0,
        }

    def _should_retry_error(self, error: Exception, context: OperationContext | None = None) -> bool:
        error_str = str(error).lower()

        decision = classify_error_for_retry(
            error,
            retry_on_404=self.retry_on_404,
            retry_on_403=self.retry_on_403,
            retry_on_500=self.retry_on_500,
        )

        if not decision.should_retry:
            return False

        if context and self.context_aware_retry:
            if context == OperationContext.FILE_CONTENT and "404" in error_str:
                return False
            elif context == OperationContext.REPOSITORY_ACCESS and "401" in error_str:
                return False
            elif context == OperationContext.BATCH_OPERATION and "timeout" in error_str:
                return True

        return True

    def _is_rate_limit_error(self, error: Exception) -> bool:
        return is_rate_limit_error_classifier(error)

    def _calculate_retry_delay(
        self,
        attempt: int,
        error: Exception,
        base_delay: float,
        backoff_multiplier: float,
        use_adaptive: bool,
        rate_limit_info: RateLimitInfo | None,
        is_secondary_rate_limit: bool,
    ) -> float:
        return calculate_retry_delay_impl(
            attempt=attempt,
            error=error,
            base_delay=base_delay,
            backoff_multiplier=backoff_multiplier,
            rate_limit_info=rate_limit_info,
            is_secondary_rate_limit=is_secondary_rate_limit,
            use_adaptive=use_adaptive,
            secondary_backoff=self.secondary_rate_limit_backoff,
            reset_buffer=self.rate_limit_reset_buffer,
            health_tracker=self._health_tracker,
            max_adaptive_delay=self.max_adaptive_delay,
        )

    def _record_success(self, start_time: float):
        duration = time.time() - start_time

        if self._circuit_breaker:
            self._circuit_breaker.record_success()

        if self._health_tracker:
            self._health_tracker.record_call(duration, success=True)

    def _record_failure(self, error: Exception):
        if self._circuit_breaker:
            self._circuit_breaker.record_failure()

        if self._health_tracker:
            error_type = categorize_error(error)
            self._health_tracker.record_call(0.0, success=False, error_type=error_type)

    def _log_permanent_failure(self, error: Exception, should_retry: bool, is_last_attempt: bool):
        if not should_retry:
            error_msg = str(error)
            if len(error_msg) > 150:
                error_msg = error_msg[:147] + "..."
            message = f"Permanent failure (no retry configured): {error_msg}"
            self._log_at_level(message, self.permanent_failure_log_level)
        elif is_last_attempt:
            message = f"All retry attempts exhausted: {str(error)[:100]}..."
            self._log_at_level(message, "ERROR")

    def _log_at_level(self, message: str, level: str):
        logger = self._logger
        level = level.upper()
        if level == "DEBUG":
            logger.debug(message)
        elif level == "INFO":
            logger.info(message)
        elif level == "WARNING":
            logger.warning(message)
        elif level == "ERROR":
            logger.error(message)
        elif level == "CRITICAL":
            logger.critical(message)
        else:
            logger.info(message)

    def get_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "circuit_breaker_enabled": self.circuit_breaker_enabled,
            "adaptive_retry_enabled": self.adaptive_retry_enabled,
            "api_health_tracking": self.api_health_tracking,
            "context_aware_retry": self.context_aware_retry,
        }

        if self._circuit_breaker:
            stats["circuit_breaker"] = self._circuit_breaker.get_stats()

        if self._health_tracker:
            stats["api_health"] = self._health_tracker.get_stats()

        return stats
