"""GitHub pull request URL parsing and validation utilities."""

from __future__ import annotations

import re

from prdiffer.domain.exceptions import InvalidURLError, InvalidPRNumberError


def parse_github_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL to extract owner, repository, and PR number.

    Supports both path formats:
    - https://github.com/owner/repo/pull/123
    - https://github.com/owner/repo/pulls/123

    Args:
        pr_url: The GitHub pull request URL to parse

    Returns:
        tuple[str, str, int]: (owner, repository_name, pr_number)

    Raises:
        InvalidURLError: If URL format is invalid or components are malformed
        InvalidPRNumberError: If PR number is not a valid integer
    """
    if not pr_url:
        raise InvalidURLError("PR URL cannot be None or empty")

    pr_url = pr_url.strip()

    if not pr_url:
        raise InvalidURLError("PR URL cannot be empty or whitespace-only")

    # Check URL length (prevent DoS)
    if len(pr_url) > 2000:
        raise InvalidURLError("URL too long (max 2000 characters)")

    if not pr_url.startswith("https://github.com/"):
        raise InvalidURLError(
            "URL must start with https://github.com/",
            details={"url": pr_url[:100]},
        )

    pattern = re.compile(r"^https://github\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9._-]+)/pulls?/(\d+)/?$")

    match = pattern.match(pr_url)

    if not match:
        raise InvalidURLError(
            "Invalid GitHub PR URL format. Expected: https://github.com/owner/repo/pull/123 or https://github.com/owner/repo/pulls/123",
            details={"url": pr_url[:100]},
        )

    owner, repo_name, pr_number_str = match.groups()

    _validate_owner(owner)
    _validate_repo_name(repo_name)

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        raise InvalidPRNumberError(f"Invalid PR number: {pr_number_str}")

    if pr_number <= 0:
        raise InvalidPRNumberError("PR number must be positive")

    if pr_number > 1000000:
        raise InvalidPRNumberError("PR number too large (max 1000000)")

    return owner, repo_name, pr_number


def _validate_owner(owner: str) -> None:
    """Validate GitHub owner/organization name."""
    if not owner:
        raise InvalidURLError("Owner cannot be empty")

    if len(owner) > 39:  # GitHub's max username length
        raise InvalidURLError("Owner name too long (max 39 characters)")

    # GitHub usernames: alphanumeric, hyphens, underscores
    if not re.match(r"^[a-zA-Z0-9_-]+$", owner):
        raise InvalidURLError(
            "Owner contains invalid characters (allowed: a-z, A-Z, 0-9, -, _)",
            details={"owner": owner},
        )


def _validate_repo_name(repo: str) -> None:
    """Validate repository name."""
    if not repo:
        raise InvalidURLError("Repository name cannot be empty")

    if len(repo) > 100:  # GitHub's max repo name length
        raise InvalidURLError("Repository name too long (max 100 characters)")

    # GitHub repo names: alphanumeric, periods, hyphens, underscores
    if not re.match(r"^[a-zA-Z0-9._-]+$", repo):
        raise InvalidURLError(
            "Repository name contains invalid characters",
            details={"repo": repo},
        )


def validate_github_pr_url(pr_url: str) -> bool:
    """Validate if a URL is a valid GitHub PR URL.

    Supports both path formats:
    - https://github.com/owner/repo/pull/123
    - https://github.com/owner/repo/pulls/123

    Args:
        pr_url: The URL to validate

    Returns:
        bool: True if URL is valid, False otherwise
    """
    try:
        parse_github_pr_url(pr_url)
        return True
    except InvalidURLError, InvalidPRNumberError:
        return False
