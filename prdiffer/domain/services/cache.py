from abc import ABC, abstractmethod
from typing import Any

from prdiffer.domain.entities.pr_diff import PRDiff


class CacheServiceInterface(ABC):
    """Abstract base class for caching services.

    This interface defines the contract for caching services that provide
    commit-based caching for PR diff data with automatic invalidation.
    """

    @abstractmethod
    def get_cache_key(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """Generate a cache key for the given repository and PR.

        Args:
            repo_owner: Repository owner/organization
            repo_name: Repository name
            pr_number: Pull request number

        Returns:
            str: Cache key in format "owner/repo/pr/number"
        """
        pass

    @abstractmethod
    async def get(self, cache_key: str, current_commit_sha: str) -> PRDiff | None:
        """Get cached PR diff data if it exists and commit SHA matches.

        Args:
            cache_key: The cache key to look up
            current_commit_sha: The current head commit SHA from GitHub

        Returns:
            Optional["PRDiff"]: Cached data if valid, None otherwise
        """
        pass

    @abstractmethod
    async def get_optimistic(self, cache_key: str) -> tuple[PRDiff | None, str | None]:
        """Get cached PR diff data without commit SHA validation (optimistic lookup).

        Performance optimization: Returns cached data and its commit SHA without
        validation, allowing caller to decide whether the data is fresh enough.

        Args:
            cache_key: The cache key to look up

        Returns:
            tuple: (cached_data, cached_commit_sha) - Both None if cache miss
        """
        pass

    @abstractmethod
    async def set(self, cache_key: str, commit_sha: str, data: PRDiff) -> None:
        """Cache PR diff data with associated commit SHA.

        Args:
            cache_key: The cache key to store under
            commit_sha: The head commit SHA when this data was fetched
            data: The PRDiff data to cache
        """
        pass

    @abstractmethod
    async def invalidate(self, cache_key: str) -> None:
        """Invalidate cache for a specific PR.

        Args:
            cache_key: The cache key to invalidate
        """
        pass

    @abstractmethod
    async def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
        """Invalidate GitHub strict snapshots and legacy entries for one PR."""
        pass

    @abstractmethod
    async def invalidate_github_repository(self, owner: str, repo: str) -> None:
        """Invalidate GitHub strict snapshots and legacy entries for a repository."""
        pass

    @abstractmethod
    def get_etag(self, cache_key: str) -> str | None:
        """Get stored ETag for a cache key."""
        pass

    @abstractmethod
    def set_etag(self, cache_key: str, etag: str) -> None:
        """Cache ETag for a specific PR key.

        Args:
            cache_key: The cache key to store ETag under
            etag: The ETag value from HTTP response

        Store ETag for conditional requests.
        """
        pass

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            dict[str, Any]: Cache statistics including size and keys
        """
        pass
