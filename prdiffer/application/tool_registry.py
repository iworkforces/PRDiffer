"""Tool registration module for FastMCP server."""

import time
import hashlib
import json
from dataclasses import asdict
from typing import NoReturn

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface, LogLevel
from prdiffer.domain.interfaces.protocols import (
    RateLimiterProtocol,
    MetricsTrackerProtocol,
    AuthenticationProtocol,
)
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol
from prdiffer.domain.interfaces.request_coalescing import RequestCoalescingProtocol
from prdiffer.application.provider_resolver import ProviderCapabilityResolver
from prdiffer.application.pr_diff_executor import CoalescedPRDiffExecutionMixin
from prdiffer.application.tool_outcomes import record_outcome

from prdiffer.domain.exceptions import (
    InvalidURLError,
    InvalidRepositoryError,
    InvalidPRNumberError,
    InputSanitizationError,
    SuspiciousOperationError,
    ValidationError,
    AuthenticationError,
    FullDiffIncompleteError,
    GitHubAPIError,
    ProviderCapabilityUnavailableError,
    RateLimitError,
)
from prdiffer.domain.error_codes import (
    E1001_INVALID_URL,
    E2002_AUTH_FAILED,
    E3001_RATE_LIMITED,
    E5002_GITHUB_API_ERROR,
    E5020_FULL_DIFF_INCOMPLETE,
    E5022_PROVIDER_CAPABILITY_UNAVAILABLE,
)

__all__ = ["ToolRegistry"]


class ToolRegistry(CoalescedPRDiffExecutionMixin):
    """Registry for FastMCP tools."""

    def __init__(
        self,
        cache_service: CacheServiceInterface,
        logger: LoggerServiceInterface,
        rate_limiter: RateLimiterProtocol,
        metrics_tracker: MetricsTrackerProtocol,
        provider_resolver: ProviderCapabilityResolver,
        authentication: AuthenticationProtocol | None = None,
        input_validator: InputValidatorProtocol | None = None,
        request_coalescing_service: RequestCoalescingProtocol | None = None,
        pr_diff_request_timeout_seconds: float | None = None,
    ):
        self._cache_service = cache_service
        self._logger = logger
        self._provider_resolver = provider_resolver
        self._rate_limiter = rate_limiter
        self._metrics_tracker = metrics_tracker
        self._pr_diff_request_timeout_seconds = pr_diff_request_timeout_seconds
        self._authentication = authentication

        if input_validator is None:
            from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

            self._input_validator = get_infrastructure_factory().create_input_validator()
        else:
            self._input_validator = input_validator

        if request_coalescing_service is None:
            from prdiffer.infrastructure.utils.coalescing_service import (
                get_request_coalescing_service,
            )

            self._request_coalescing = get_request_coalescing_service()
        else:
            self._request_coalescing = request_coalescing_service

    def _generate_request_id(self) -> str:
        return self._metrics_tracker.generate_request_id()

    def _check_rate_limit(self, client_id: str = "global"):
        if not self._rate_limiter.check_rate_limit(client_id):
            rate_info = self._rate_limiter.get_rate_limit_info()
            raise RateLimitError(
                f"Rate limit exceeded for client '{client_id}'. Maximum {rate_info['max_requests']} requests per {rate_info['window_seconds']} seconds.",
                error_code=E3001_RATE_LIMITED,
            )
        self._rate_limiter.increment_rate_limit(client_id)

    async def _authenticate_request(self, request_id: str, start_time: float, api_key: str | None, *, operation: str) -> str | None:
        try:
            if self._authentication is None:
                raise AuthenticationError(
                    "Authentication service not configured",
                    error_code=E2002_AUTH_FAILED,
                )
            try:
                request = get_http_request()
            except RuntimeError as error:
                if str(error) != "No active HTTP request found.":
                    raise
                source = "stdio:local"
            else:
                if request.client is None or not request.client.host.strip():
                    raise AuthenticationError("HTTP transport peer unavailable", error_code=E2002_AUTH_FAILED)
                source = "http:" + request.client.host
            is_authenticated, client_id = self._authentication.authenticate(api_key, source=source)
            if not is_authenticated:
                raise AuthenticationError(
                    "Authentication failed. Please provide a valid API key via 'api_key' parameter.",
                    error_code=E2002_AUTH_FAILED,
                )
        except AuthenticationError as error:
            self._logger.warning(
                "Authentication failed",
                request_id=request_id,
                error=str(error),
            )
            raise

        return client_id

    def _create_safe_error_message(self, exception: Exception) -> str:
        safe_messages = {
            "GithubException": "GitHub API error occurred",
            "RateLimitExceededException": "API rate limit exceeded. Please try again later",
            "UnknownObjectException": "Repository or PR not found",
            "BadCredentialsException": "GitHub authentication failed",
            "TwoFactorException": "Two-factor authentication required",
            "InvalidURLError": "Invalid PR or merge request URL",
            "InvalidRepositoryError": "Invalid repository identifier",
            "InvalidPRNumberError": "Invalid pull request number",
            "InputSanitizationError": "Invalid input parameters",
            "SuspiciousOperationError": "Request contains suspicious patterns",
            "ConnectionError": "Connection to the VCS provider failed",
            "TimeoutError": "Request timed out",
            "SSLError": "Secure connection failed",
            "ValueError": "Invalid input value",
            "TypeError": "Invalid input type",
            "KeyError": "Missing required field",
            "AttributeError": "Configuration error",
        }

        exception_type = type(exception).__name__

        if exception_type in safe_messages:
            return safe_messages[exception_type]

        return "Request processing failed"

    def _log_metrics_and_return_success(self, start_time: float, pr_diff: PRDiff) -> PRDiff:
        diff_size = len(pr_diff.files)
        diff_hash = hashlib.md5(str(pr_diff.files).encode()).hexdigest()[:8]

        self._logger.info(f"Successfully fetched PR diff - files: {diff_size}, hash: {diff_hash}...")

        if self._logger.should_log(LogLevel.DEBUG):
            sanitized_preview = self._input_validator.sanitize_for_logging(
                f"Files: {len(pr_diff.files)}, preview: {pr_diff.files[:2] if pr_diff.files else []}",
                max_length=500,
            )
            self._logger.debug(f"PR diff content preview (sanitized): {sanitized_preview}")
            sanitized_json = self._input_validator.sanitize_for_logging(
                json.dumps(asdict(pr_diff), indent=2),
                max_length=2000,
            )
            self._logger.debug(f"PR Diff (Pretty JSON, sanitized):\n{sanitized_json}")

        return pr_diff

    def _handle_security_exception(
        self,
        exception: Exception,
        start_time: float,
        request_id: str,
        pr_url: str,
        *,
        operation: str = "get_pr_diff",
    ) -> NoReturn:
        self._logger.warning(
            f"Security validation error in {operation} request",
            request_id=request_id,
            pr_url=self._input_validator.sanitize_for_logging(pr_url) if pr_url else None,
            error=str(exception),
            error_type=type(exception).__name__,
        )

        safe_message = self._create_safe_error_message(exception)
        raise ValidationError(f"Invalid request: {safe_message}", error_code=E1001_INVALID_URL)

    def _handle_validation_exception(
        self,
        exception: Exception,
        start_time: float,
        request_id: str,
        pr_url: str,
        *,
        operation: str = "get_pr_diff",
    ) -> NoReturn:
        self._logger.warning(
            f"Validation error in {operation} request",
            request_id=request_id,
            pr_url=self._input_validator.sanitize_for_logging(pr_url) if pr_url else None,
            error=str(exception),
        )

        safe_message = self._create_safe_error_message(exception)
        raise ValidationError(f"Invalid request: {safe_message}", error_code=E1001_INVALID_URL)

    def _handle_runtime_exception(
        self,
        exception: Exception,
        start_time: float,
        request_id: str,
        pr_url: str,
        *,
        operation: str = "get_pr_diff",
    ) -> NoReturn:
        self._logger.error(
            f"Failed to complete {operation}",
            request_id=request_id,
            pr_url=self._input_validator.sanitize_for_logging(pr_url) if pr_url else None,
            error=str(exception),
            error_type=type(exception).__name__,
        )

        safe_message = self._create_safe_error_message(exception)
        raise GitHubAPIError(
            f"Failed to complete {operation}: {safe_message}",
            error_code=E5002_GITHUB_API_ERROR,
        )

    def _handle_capability_exception(self, exception: ProviderCapabilityUnavailableError, start_time: float, operation: str) -> NoReturn:
        """Return the stable unsupported-capability error."""
        raise ToolError(str(E5022_PROVIDER_CAPABILITY_UNAVAILABLE)) from exception

    def register_tools(self, mcp: FastMCP) -> None:

        @mcp.tool()
        @record_outcome(self._metrics_tracker, "get_pr_diff")
        async def get_pr_diff(pr_url: str, api_key: str | None = None) -> PRDiff:
            """Get a complete structured full-context GitHub PR/GitLab MR diff (all-or-nothing).

            Successful responses include every selected file in provider order with:
            - ``path`` / optional ``previous_path`` (renames only)
            - ``status`` (added, modified, deleted, renamed)
            - ``stats`` (additions/deletions)
            - ``diff``: **generated full-context** unified text (not a hunk-only provider patch)

            Completeness is strict: if any selected file cannot be fully reconstructed
            (inventory truncation, file count limit, binary/oversized/undecodable content,
            unsupported status, generation failure, or response size limit), the tool fails
            with ``E5020_FULL_DIFF_INCOMPLETE`` and a stable ``reason`` — never a partial
            ``files`` array.

            Args:
                pr_url: GitHub PR or GitLab MR URL
                api_key: Optional API key when server authentication is enabled

            Raises:
                Authentication/validation/rate-limit errors for request gate failures
                GitHubAPIError / FullDiffIncompleteError for provider and completeness failures
            """
            request_id = self._generate_request_id()
            start_time = time.time()

            self._logger.info(
                "Processing get_pr_diff request",
                request_id=request_id,
                pr_url=pr_url,
            )

            client_id = await self._authenticate_request(request_id, start_time, api_key, operation="get_pr_diff")

            rate_limit_client_id = client_id or "anonymous"

            try:
                self._check_rate_limit(rate_limit_client_id)

                if not pr_url:
                    raise InputSanitizationError("PR URL parameter is required")
                sanitized_pr_url = self._input_validator.sanitize_string(pr_url, max_length=2000)
                target = self._provider_resolver.resolve_target(sanitized_pr_url, self._input_validator)
                capability = self._provider_resolver.resolve_strict_diff(target)
                pr_diff = await self._execute_use_case_with_coalescing(
                    target.repo_owner,
                    target.repo_name,
                    target.pr_number,
                    pr_diff_reader=capability.reader,
                    cache_namespace=capability.cache_namespace,
                    base_url=target.base_url,
                )

                return self._log_metrics_and_return_success(start_time, pr_diff)

            except FullDiffIncompleteError as e:
                # Preserve machine-readable E5020 at the raw FastMCP boundary.
                payload: dict[str, object] = {
                    "error_code": str(E5020_FULL_DIFF_INCOMPLETE),
                    "message": e.message,
                    "details": e.details,
                }
                raise ToolError(json.dumps(payload, separators=(",", ":"), sort_keys=False)) from e

            except ProviderCapabilityUnavailableError as e:
                self._handle_capability_exception(e, start_time, "get_pr_diff")

            except (
                InvalidURLError,
                InvalidRepositoryError,
                InvalidPRNumberError,
                InputSanitizationError,
                SuspiciousOperationError,
            ) as e:
                self._handle_security_exception(e, start_time, request_id, pr_url)

            except ValueError as e:
                self._handle_validation_exception(e, start_time, request_id, pr_url)

            except (
                RuntimeError,
                KeyError,
                AttributeError,
                TypeError,
                ConnectionError,
            ) as e:
                self._handle_runtime_exception(e, start_time, request_id, pr_url)

        _ = get_pr_diff  # registered via @mcp.tool() decorator

        @mcp.tool()
        @record_outcome(self._metrics_tracker, "approve_pr")
        async def approve_pr(pr_url: str, compliment: str, api_key: str | None = None) -> str:
            """Approve a GitHub PR or GitLab MR with a compliment comment/note.

            Args:
                pr_url: GitHub PR URL (e.g. https://github.com/owner/repo/pull/123)
                    or GitLab MR URL (e.g. https://gitlab.com/group/project/-/merge_requests/42)
                compliment: Non-empty compliment text included in the approval review (GitHub)
                    or as a note after approve (GitLab)
                api_key: Optional API key for authentication (required if authentication is enabled)

            Returns:
                str: Success message indicating the GitHub PR/GitLab MR was approved

            Raises:
                ValidationError: If the URL is invalid or compliment is empty
                AuthenticationError / RateLimitError / provider API errors on failure
            """
            request_id = self._generate_request_id()
            start_time = time.time()

            self._logger.info(
                "Processing approve_pr request",
                request_id=request_id,
                pr_url=pr_url[:100] if pr_url else pr_url,
            )

            client_id = await self._authenticate_request(request_id, start_time, api_key, operation="approve_pr")

            rate_limit_client_id = client_id or "anonymous"

            try:
                self._check_rate_limit(rate_limit_client_id)

                if not pr_url:
                    raise InputSanitizationError("PR URL parameter is required")
                sanitized_pr_url = self._input_validator.sanitize_string(pr_url, max_length=2000)
                target = self._provider_resolver.resolve_target(sanitized_pr_url, self._input_validator)

                if not isinstance(compliment, str) or not compliment.strip():
                    raise ValidationError(
                        "Compliment must be a non-empty string",
                        error_code=E1001_INVALID_URL,
                    )
                compliment = compliment.strip()

                capability = self._provider_resolver.resolve_approval(target)
                result = await capability.approve(target, compliment)

                self._logger.info(f"Successfully approved PR\n{result}")
                return result

            except (
                InvalidURLError,
                InvalidRepositoryError,
                InvalidPRNumberError,
                InputSanitizationError,
                SuspiciousOperationError,
            ) as e:
                self._handle_security_exception(e, start_time, request_id, pr_url, operation="approve_pr")

            except ValueError as e:
                self._handle_validation_exception(e, start_time, request_id, pr_url, operation="approve_pr")

            except ProviderCapabilityUnavailableError as e:
                self._handle_capability_exception(e, start_time, "approve_pr")

            except (
                RuntimeError,
                KeyError,
                AttributeError,
                TypeError,
                ConnectionError,
            ) as e:
                self._handle_runtime_exception(e, start_time, request_id, pr_url, operation="approve_pr")

        _ = approve_pr  # registered via @mcp.tool() decorator

        @mcp.tool()
        @record_outcome(self._metrics_tracker, "describe_pr")
        async def describe_pr(pr_url: str, pr_description: str, api_key: str | None = None) -> str:
            """Update a GitHub PR or GitLab MR description/body.

            Args:
                pr_url: GitHub PR URL (e.g. https://github.com/owner/repo/pull/123)
                    or GitLab MR URL (e.g. https://gitlab.com/group/project/-/merge_requests/42)
                pr_description: Non-empty description text to set on the GitHub PR/GitLab MR
                api_key: Optional API key for authentication (required if authentication is enabled)

            Returns:
                str: Success message indicating the description was updated

            Raises:
                ValidationError: If the URL is invalid or description is empty
                AuthenticationError / RateLimitError / provider API errors on failure
            """
            request_id = self._generate_request_id()
            start_time = time.time()

            self._logger.info(
                "Processing describe_pr request",
                request_id=request_id,
                pr_url=pr_url[:100] if pr_url else pr_url,
            )

            client_id = await self._authenticate_request(request_id, start_time, api_key, operation="describe_pr")

            rate_limit_client_id = client_id or "anonymous"

            try:
                self._check_rate_limit(rate_limit_client_id)

                if not pr_url:
                    raise InputSanitizationError("PR URL parameter is required")
                sanitized_pr_url = self._input_validator.sanitize_string(pr_url, max_length=2000)
                target = self._provider_resolver.resolve_target(sanitized_pr_url, self._input_validator)

                if not isinstance(pr_description, str) or not pr_description.strip():
                    raise ValidationError(
                        "PR description must be a non-empty string",
                        error_code=E1001_INVALID_URL,
                    )
                pr_description = pr_description.strip()

                capability = self._provider_resolver.resolve_description(target)
                result = await capability.describe(target, pr_description)

                self._logger.info(f"Successfully updated PR description\n{result}")
                return result

            except (
                InvalidURLError,
                InvalidRepositoryError,
                InvalidPRNumberError,
                InputSanitizationError,
                SuspiciousOperationError,
            ) as e:
                self._handle_security_exception(e, start_time, request_id, pr_url, operation="describe_pr")

            except ValueError as e:
                self._handle_validation_exception(e, start_time, request_id, pr_url, operation="describe_pr")

            except ProviderCapabilityUnavailableError as e:
                self._handle_capability_exception(e, start_time, "describe_pr")

            except (
                RuntimeError,
                KeyError,
                AttributeError,
                TypeError,
                ConnectionError,
            ) as e:
                self._handle_runtime_exception(e, start_time, request_id, pr_url, operation="describe_pr")

        _ = describe_pr  # registered via @mcp.tool() decorator
