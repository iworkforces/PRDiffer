"""Repository cache service interface for domain layer.

This module defines the abstract interface for repository caching services
that cache PRDiffRepositoryInterface instances to avoid repeated initialization.
"""

from abc import ABC, abstractmethod
from typing import Any

from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface


class RepositoryCacheServiceInterface(ABC):
    """Abstract base class for repository caching services.

    This interface defines the contract for services that cache
    PRDiffRepositoryInterface instances to optimize GitHub API usage.
    """

    @abstractmethod
    def insert(self, repository: PRDiffRepositoryInterface) -> bool:
        """Insert a repository instance into the cache.

        Args:
            repository: PRDiffRepositoryInterface instance to cache

        Returns:
            bool: True if inserted successfully, False otherwise
        """
        pass

    @abstractmethod
    def retrieve(self, repo_owner: str, repo_name: str, pr_number: int) -> PRDiffRepositoryInterface | None:
        """Retrieve a cached repository instance.

        Args:
            repo_owner: Repository owner/organization
            repo_name: Repository name
            pr_number: Pull request number

        Returns:
            PRDiffRepositoryInterface instance if found and valid, None otherwise
        """
        pass

    @abstractmethod
    def validate(self, repo_owner: str, repo_name: str, pr_number: int) -> bool:
        """Validate if a repository instance exists and is valid in the cache.

        Args:
            repo_owner: Repository owner/organization
            repo_name: Repository name
            pr_number: Pull request number

        Returns:
            bool: True if valid cached instance exists, False otherwise
        """
        pass

    @abstractmethod
    def remove(self, repo_owner: str, repo_name: str, pr_number: int) -> bool:
        """Remove a repository instance from the cache.

        Args:
            repo_owner: Repository owner/organization
            repo_name: Repository name
            pr_number: Pull request number

        Returns:
            bool: True if removed successfully, False if not found
        """
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all entries from the cache."""
        pass

    @abstractmethod
    def size(self) -> int:
        """Get the current number of entries in the cache.

        Returns:
            int: Number of cached repository instances
        """
        pass

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict containing cache statistics
        """
        pass

    @abstractmethod
    def invalidate(self, cache_key: str) -> bool:
        """Invalidate a cache entry by key.

        Args:
            cache_key: Cache key to invalidate (format: "owner/repo" or "owner/repo/pr/number")

        Returns:
            bool: True if invalidated successfully, False if not found
        """
        pass

    @abstractmethod
    def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
        """Remove the cached GitHub repository instance for one PR, if present."""
        pass

    @abstractmethod
    def invalidate_github_repository(self, owner: str, repo: str) -> None:
        """Remove all cached GitHub PR instances for a repository."""
        pass
