"""Retry handler factory functions."""

import logging

from prdiffer.infrastructure.utils.retry.handler import UnifiedRetryHandler


def get_retry_handler(
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
) -> UnifiedRetryHandler:
    """Get a configured retry handler instance."""
    return UnifiedRetryHandler(
        max_retries=max_retries,
        retry_delay=retry_delay,
        retry_on_404=retry_on_404,
        retry_on_403=retry_on_403,
        retry_on_500=retry_on_500,
        retry_log_level=retry_log_level,
        permanent_failure_log_level=permanent_failure_log_level,
        circuit_breaker_enabled=circuit_breaker_enabled,
        circuit_breaker_failure_threshold=circuit_breaker_failure_threshold,
        circuit_breaker_timeout=circuit_breaker_timeout,
        adaptive_retry_enabled=adaptive_retry_enabled,
        max_adaptive_delay=max_adaptive_delay,
        rate_limit_remaining_threshold=rate_limit_remaining_threshold,
        rate_limit_reset_buffer=rate_limit_reset_buffer,
        secondary_rate_limit_backoff=secondary_rate_limit_backoff,
        api_health_tracking=api_health_tracking,
        context_aware_retry=context_aware_retry,
        logger=logger,
    )
