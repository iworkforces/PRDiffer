"""GitHub API service interface for domain layer."""

from abc import ABC, abstractmethod


class GitHubAPIServiceInterface(ABC):
    """Abstract base class for GitHub API services.

    The strict full-diff path reads immutable git trees/blobs through the
    provider SDK objects obtained after client initialization.
    """

    @abstractmethod
    def initialize_client(self, github_token: str | None = None, timeout: int = 30) -> None:
        """Initialize the GitHub client with authentication.

        Args:
            github_token: GitHub personal access token for authentication
            timeout: API timeout in seconds
        """
        pass
