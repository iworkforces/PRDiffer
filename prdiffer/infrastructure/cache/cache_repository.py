"""Repository cache service for GitHub repository instances.

This module combines cache entry models and the RepositoryCacheService.
"""

import time
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Any, ParamSpec, TypeVar
from collections.abc import Callable

from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface
from prdiffer.domain.services.repository_cache import RepositoryCacheServiceInterface
from prdiffer.infrastructure.logging.console_logger import get_logger

P = ParamSpec("P")
R = TypeVar("R")

__all__ = [
    "CacheEntry",
    "with_lock",
    "RepositoryCacheService",
    "get_repository_cache_service",
]


# ---------------------------------------------------------------------------
# Models and utilities (originally in repository/models.py)
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    repository: PRDiffRepositoryInterface
    timestamp: float
    initialized: bool


def with_lock(lock_attr: str = "_lock") -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            self = args[0]
            lock = getattr(self, lock_attr)
            with lock:
                return func(*args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# RepositoryCacheService (originally in repository/service.py)
# ---------------------------------------------------------------------------


class RepositoryCacheService(RepositoryCacheServiceInterface):
    """Service for caching and reusing GitHubPRDiffRepository instances.

    This service maintains a cache of repository instances keyed by
    (repo_owner, repo_name, pr_number) to avoid repeated GitHub API
    initialization for the same repositories.
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self._cache: dict[tuple[str, str, int], CacheEntry] = {}
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._lock = RLock()
        self._logger = get_logger()

    def _get_cache_key(self, repo_owner: str, repo_name: str, pr_number: int) -> tuple[str, str, int]:
        return (repo_owner.lower(), repo_name.lower(), pr_number)

    @with_lock()
    def _clean_expired_entries(self) -> None:
        current_time = time.time()
        expired_keys: list[tuple[str, str, int]] = []

        for key, entry in self._cache.items():
            if current_time - entry.timestamp > self._ttl_seconds:
                expired_keys.append(key)

        for key in expired_keys:
            del self._cache[key]
            self._logger.debug(
                "Removed expired repository from cache",
                repo_owner=key[0],
                repo_name=key[1],
                pr_number=key[2],
            )

    @with_lock()
    def _evict_if_needed(self) -> None:
        if len(self._cache) <= self._max_size:
            return

        entries_list = list(self._cache.items())
        excess_count = len(entries_list) - self._max_size
        entries_to_remove = sorted(entries_list, key=lambda x: x[1].timestamp)[:excess_count]

        for key, _ in entries_to_remove:
            del self._cache[key]
            self._logger.debug(
                "Evicted repository from cache due to size limit",
                repo_owner=key[0],
                repo_name=key[1],
                pr_number=key[2],
            )

    def _is_entry_valid(self, entry: CacheEntry, current_time: float) -> bool:
        return (current_time - entry.timestamp <= self._ttl_seconds) and entry.initialized

    def _get_valid_entry(self, cache_key: tuple[str, str, int], extend_ttl: bool = False) -> CacheEntry | None:
        if cache_key not in self._cache:
            return None

        entry = self._cache[cache_key]
        current_time = time.time()

        if not self._is_entry_valid(entry, current_time):
            del self._cache[cache_key]
            self._logger.debug(
                "Repository cache entry invalid or expired",
                repo_owner=cache_key[0],
                repo_name=cache_key[1],
                pr_number=cache_key[2],
            )
            return None

        if extend_ttl:
            entry.timestamp = current_time

        return entry

    @with_lock()
    def insert(self, repository: PRDiffRepositoryInterface) -> bool:
        cache_key = self._get_cache_key(repository.repo_owner, repository.repo_name, repository.pr_number)

        self._clean_expired_entries()
        self._evict_if_needed()

        self._cache[cache_key] = CacheEntry(
            repository=repository,
            timestamp=time.time(),
            initialized=getattr(repository, "_initialized", False),
        )

        self._logger.debug(
            "Repository cached successfully",
            repo_owner=repository.repo_owner,
            repo_name=repository.repo_name,
            pr_number=repository.pr_number,
        )
        return True

    @with_lock()
    def retrieve(self, repo_owner: str, repo_name: str, pr_number: int) -> PRDiffRepositoryInterface | None:
        cache_key = self._get_cache_key(repo_owner, repo_name, pr_number)

        entry = self._get_valid_entry(cache_key, extend_ttl=True)
        if entry is None:
            return None

        self._logger.debug(
            "Repository retrieved from cache",
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number,
        )
        return entry.repository

    @with_lock()
    def validate(self, repo_owner: str, repo_name: str, pr_number: int) -> bool:
        cache_key = self._get_cache_key(repo_owner, repo_name, pr_number)

        entry = self._get_valid_entry(cache_key, extend_ttl=False)
        return entry is not None

    @with_lock()
    def remove(self, repo_owner: str, repo_name: str, pr_number: int) -> bool:
        cache_key = self._get_cache_key(repo_owner, repo_name, pr_number)

        if cache_key in self._cache:
            del self._cache[cache_key]
            self._logger.debug(
                "Repository removed from cache",
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
            )
            return True
        return False

    @with_lock()
    def clear(self) -> None:
        cache_size = len(self._cache)
        self._cache.clear()
        self._logger.info(f"Cleared repository cache ({cache_size} entries)")

    @with_lock()
    def size(self) -> int:
        return len(self._cache)

    @with_lock()
    def stats(self) -> dict[str, Any]:
        current_time = time.time()
        initialized_count = 0
        expired_count = 0

        for entry in self._cache.values():
            if entry.initialized:
                initialized_count += 1
            if current_time - entry.timestamp > self._ttl_seconds:
                expired_count += 1

        return {
            "total_entries": len(self._cache),
            "initialized_entries": initialized_count,
            "expired_entries": expired_count,
            "max_size": self._max_size,
            "ttl_seconds": self._ttl_seconds,
        }

    @with_lock()
    def invalidate(self, cache_key: str) -> bool:
        parts = cache_key.split("/")
        if len(parts) == 2:
            repo_owner, repo_name = parts
            return self.remove(repo_owner, repo_name, 0)
        elif len(parts) == 4 and parts[2] == "pr":
            repo_owner, repo_name, _, pr_number_str = parts
            try:
                pr_number = int(pr_number_str)
                return self.remove(repo_owner, repo_name, pr_number)
            except ValueError:
                self._logger.warning(
                    "Invalid PR number in cache key",
                    cache_key=cache_key,
                )
                return False
        else:
            self._logger.warning(
                "Invalid cache key format",
                cache_key=cache_key,
            )
            return False

    @with_lock()
    def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
        self._cache.pop(self._get_cache_key(owner, repo, pr_number), None)

    @with_lock()
    def invalidate_github_repository(self, owner: str, repo: str) -> None:
        repo_key = (owner.lower(), repo.lower())
        for cache_key in tuple(self._cache):
            if cache_key[:2] == repo_key:
                del self._cache[cache_key]


_repository_cache_service: RepositoryCacheService | None = None


def get_repository_cache_service() -> RepositoryCacheService:
    global _repository_cache_service
    if _repository_cache_service is None:
        _repository_cache_service = RepositoryCacheService()
    return _repository_cache_service
