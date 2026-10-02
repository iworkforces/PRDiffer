"""GitHub exception handling helper for repository write operations."""

from __future__ import annotations

from github.GithubException import GithubException

from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)


def handle_github_exception(
    e: GithubException,
    logger: LoggerServiceInterface,
    *,
    pr_number: int,
    repo_owner: str,
    repo_name: str,
    operation: str,
) -> None:
    """Handle common GitHub API exceptions with consistent error logging and re-raise.

    Checks for 404, 403, and 429 errors in order, logs appropriately,
    and raises RuntimeError with a descriptive message.

    Args:
        e: The GithubException to handle
        logger: Logger service for structured logging
        pr_number: Pull request number for context
        repo_owner: Repository owner for context
        repo_name: Repository name for context
        operation: Human-readable operation name (e.g., "approving", "updating description for")

    Raises:
        RuntimeError: Always raised with context-appropriate message
    """
    sanitized = sanitize_exception_for_logging(e)
    error_str = str(e).lower()

    if "404" in error_str or "not found" in error_str:
        logger.error(
            f"Pull request #{pr_number} not found in {repo_owner}/{repo_name}",
            extra=sanitized,
            pr_number=pr_number,
        )
        raise RuntimeError(f"Pull request #{pr_number} not found in repository {repo_owner}/{repo_name}") from e

    if "403" in error_str or "forbidden" in error_str:
        logger.error(
            f"Permission denied for PR #{pr_number} - insufficient permissions",
            extra=sanitized,
            pr_number=pr_number,
        )
        op_clean = operation.replace("ing", "").rstrip("t") if operation.endswith("ting") else operation
        raise RuntimeError(f"Insufficient permissions to {op_clean} PR #{pr_number} - ensure token has 'repo' scope and write access") from e

    if "429" in error_str or "rate limit" in error_str:
        logger.warning(
            f"GitHub API rate limit exceeded while {operation} PR #{pr_number}",
            extra=sanitized,
            pr_number=pr_number,
        )
        raise RuntimeError("GitHub API rate limit exceeded - please retry later") from e

    logger.error(
        f"GitHub API error while {operation} PR #{pr_number}",
        extra=sanitized,
        pr_number=pr_number,
    )
    raise RuntimeError(f"GitHub API error while {operation} PR #{pr_number}") from e
