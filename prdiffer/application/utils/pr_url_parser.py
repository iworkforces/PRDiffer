"""Shared utility for parsing GitHub PR and GitLab MR URLs for MCP tools."""

from dataclasses import dataclass
from typing import Literal

from prdiffer.domain.entities.gitlab_merge_request_url import parse_gitlab_merge_request_parts
from prdiffer.domain.exceptions import (
    InvalidURLError,
)
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol


@dataclass(frozen=True, slots=True)
class PRTarget:
    """A validated pull or merge request target."""

    provider: Literal["github", "gitlab"]
    repo_owner: str
    repo_name: str
    pr_number: int
    # GitLab base URL (e.g. https://gitlab.com or https://gitlab.example.com); None for GitHub.
    base_url: str | None = None


def normalize_request_url(pr_url: object) -> str:
    """Require a string URL and strip surrounding whitespace.

    Shared by ``parse_pr_url``, ``parse_pr_target``, and MCP provider routing so
    prefix ownership checks see the same normalized form as validation.
    """
    if not isinstance(pr_url, str):
        raise InvalidURLError(f"PR URL must be a string, got {type(pr_url).__name__}")

    stripped = pr_url.strip()
    if not stripped:
        raise InvalidURLError("PR URL cannot be empty or whitespace-only")
    return stripped


def parse_pr_url(
    pr_url: object,
    input_validator: InputValidatorProtocol,
) -> tuple[str, str, int]:
    """Parse GitHub PR URL to extract repository owner, name, and PR number.

    Args:
        pr_url: The GitHub pull request URL to parse
        input_validator: Required validator implementing InputValidatorProtocol.

    Returns:
        tuple[str, str, int]: (repo_owner, repo_name, pr_number)

    Raises:
        InvalidURLError: If the URL format is invalid, contains invalid characters,
            or is empty/whitespace-only, or not a string
        SuspiciousOperationError: If the URL contains suspicious patterns
        InvalidRepositoryError: If repository name is invalid
        InvalidPRNumberError: If PR number is invalid

    Examples:
        >>> parse_pr_url("https://github.com/owner/repo/pull/123", input_validator)
        ('owner', 'repo', 123)

        >>> parse_pr_url("https://github.com/owner/repo/pulls/456", input_validator)
        ('owner', 'repo', 456)
    """
    pr_url_stripped = normalize_request_url(pr_url)
    return input_validator.validate_github_url(pr_url_stripped)


def parse_pr_target(
    pr_url: object,
    input_validator: InputValidatorProtocol,
) -> PRTarget:
    """Parse a supported PR or merge request URL using the required validator."""
    pr_url_stripped = normalize_request_url(pr_url)
    if pr_url_stripped.startswith("https://github.com/"):
        repo_owner, repo_name, pr_number = parse_pr_url(pr_url_stripped, input_validator)
        return PRTarget("github", repo_owner, repo_name, pr_number, base_url=None)

    # GitLab.com or custom-hosted GitLab: HTTPS MR path marker.
    if pr_url_stripped.startswith("https://") and "/-/merge_requests/" in pr_url_stripped:
        # Validator enforces suspicious-pattern checks + path/host rules.
        repo_owner, repo_name, pr_number = input_validator.validate_gitlab_url(pr_url_stripped)
        parts = parse_gitlab_merge_request_parts(pr_url_stripped)
        return PRTarget(
            "gitlab",
            repo_owner,
            repo_name,
            pr_number,
            base_url=parts.base_url,
        )

    raise InvalidURLError("Unsupported PR URL provider")
