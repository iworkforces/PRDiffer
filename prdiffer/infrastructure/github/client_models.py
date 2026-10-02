"""GitHub API client constants and models."""

from github import GithubException

GITHUB_API_EXCEPTIONS: tuple[type[BaseException], ...] = (
    GithubException,
    TimeoutError,
    ConnectionError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
)
