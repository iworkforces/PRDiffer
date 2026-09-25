"""Cache service for GitHub PR diff data with commit-based invalidation."""

import hashlib
import time
import anyio
from collections import OrderedDict
from typing import Any, cast

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import GITHUB_FULL_DIFF_CACHE_PREFIX_V3
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.exceptions import ValidationError
from prdiffer.domain.errors import E1010_INVALID_CONFIGURATION
from prdiffer.infrastructure.logging.console_logger import get_logger


class CacheService(CacheServiceInterface):
    """Caching service for PR diff data with commit-based invalidation and LRU eviction."""

    def __init__(self):
        """Initialize the cache service with empty cache and key hashing support."""
        self._lock = anyio.Lock()

        self.cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.logger = get_logger()

        from prdiffer.infrastructure.settings import get_settings_service

        settings = get_settings_service()

        self._use_hashed_keys = settings.get("cache.use_hashed_keys", True)
        self._hash_algorithm = settings.get("cache.hash_algorithm", "md5")
        self._store_key_mapping = settings.get("cache.store_key_mapping", True)
        self._ttl = settings.get("cache.ttl", 600)
        self._cache_max_size = settings.get("cache.max_size", 1000)

        self._key_mapping: dict[str, str] = {}
        self._entry_keys: dict[str, str] = {}

        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_expirations = 0
        self._cache_evictions_ttl = 0
        self._cache_evictions_size = 0

        if self._use_hashed_keys:
            self.logger.info(f"Cache key hashing enabled (algorithm={self._hash_algorithm}, mapping={self._store_key_mapping}, ttl={self._ttl}s)")

    def get_cache_key(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """Generate a cache key for the given repository and PR."""
        return f"{repo_owner}/{repo_name}/pr/{pr_number}"

    def _hash_key(self, key: str) -> str:
        """Hash cache key using configured algorithm."""
        if self._hash_algorithm == "md5":
            return hashlib.md5(key.encode("utf-8")).hexdigest()
        elif self._hash_algorithm == "sha256":
            return hashlib.sha256(key.encode("utf-8")).hexdigest()
        elif self._hash_algorithm == "sha256_short":
            return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        else:
            raise ValidationError(
                f"Unsupported hash algorithm: {self._hash_algorithm}",
                error_code=E1010_INVALID_CONFIGURATION,
            )

    async def _get_internal_key(self, original_key: str, store_mapping: bool = False) -> tuple[str, str]:
        """Get internal key with optional hashing and store reverse mapping."""
        if not self._use_hashed_keys:
            return original_key, ""

        hashed = self._hash_key(original_key)

        if store_mapping and self._store_key_mapping:
            async with self._lock:
                self._key_mapping[hashed] = original_key

        return hashed, f"{hashed[:8]}..."

    async def _get_original_key(self, internal_key: str) -> str:
        """Get original key from internal key using reverse mapping."""
        if self._use_hashed_keys and self._store_key_mapping:
            async with self._lock:
                return self._key_mapping.get(internal_key, internal_key)
        return internal_key

    def _is_entry_expired(self, cached_data: dict[str, Any]) -> bool:
        """Check if a cache entry has expired based on TTL."""
        timestamp = cached_data.get("timestamp")
        if timestamp is None:
            return False

        age = time.time() - float(timestamp)
        return bool(age > self._ttl)

    async def _evict_oldest_if_needed(self) -> None:
        """Evict entries when cache exceeds max size.

        O(1) LRU eviction using OrderedDict.popitem(last=False).
        TTL eviction only every 10 calls to avoid O(n) scan on every set().
        """
        if not hasattr(self, "_eviction_call_count"):
            self._eviction_call_count = 0

        self._eviction_call_count += 1

        if self._eviction_call_count % 10 == 0:
            current_time = time.time()
            expired_keys: list[str] = []

            for key, entry in self.cache.items():
                timestamp = entry.get("timestamp")
                if timestamp is not None and current_time - float(timestamp) >= self._ttl:
                    expired_keys.append(key)

            for key in expired_keys:
                self.cache.pop(key)
                self._key_mapping.pop(key, None)
                self._entry_keys.pop(key, None)
                self._cache_evictions_ttl += 1

            if expired_keys:
                self.logger.debug(f"Cache eviction (TTL): removed {len(expired_keys)} expired entries [size={len(self.cache)}/{self._cache_max_size}]")

        while len(self.cache) >= self._cache_max_size:
            evicted_key, _ = self.cache.popitem(last=False)
            original_key = self._entry_keys.pop(evicted_key, evicted_key)
            self._key_mapping.pop(evicted_key, None)
            self._cache_evictions_size += 1
            self.logger.debug(f"Cache eviction (LRU): {original_key[:50]}... [size={len(self.cache)}/{self._cache_max_size}]")

    async def get(self, cache_key: str, current_commit_sha: str) -> PRDiff | None:
        """Get cached PR diff data if it exists, commit SHA matches, and not expired."""
        internal_key, hash_display = await self._get_internal_key(cache_key)

        async with self._lock:
            if internal_key not in self.cache:
                self._cache_misses += 1
                self.logger.debug(
                    "Cache miss",
                    cache_key=cache_key,
                    hash=hash_display if self._use_hashed_keys else None,
                )
                return None

            cached_data = self.cache[internal_key]

            if self._is_entry_expired(cached_data):
                self._cache_expirations += 1
                self._cache_misses += 1
                del self.cache[internal_key]
                self._key_mapping.pop(internal_key, None)
                self._entry_keys.pop(internal_key, None)
                self.logger.info(
                    "Cache entry expired (TTL)",
                    cache_key=cache_key,
                    hash=hash_display if self._use_hashed_keys else None,
                    ttl_seconds=self._ttl,
                )
                return None

            cached_commit_sha = cached_data.get("commit_sha")
            cached_result = cached_data.get("data")

            if cached_commit_sha == current_commit_sha and cached_result:
                self._cache_hits += 1
                self.cache.move_to_end(internal_key)
                self.logger.info(
                    "Cache hit",
                    cache_key=cache_key,
                    hash=hash_display if self._use_hashed_keys else None,
                    commit_sha=current_commit_sha,
                )
                return cast(PRDiff, cached_result)
            else:
                self._cache_misses += 1
                self.logger.info(
                    "Cache miss (commit SHA mismatch)",
                    cache_key=cache_key,
                    hash=hash_display if self._use_hashed_keys else None,
                    cached_sha=cached_commit_sha,
                    current_sha=current_commit_sha,
                )
                return None

    async def get_optimistic(self, cache_key: str) -> tuple[PRDiff | None, str | None]:
        """Get cached PR diff data without commit SHA validation (optimistic lookup).

        Returns cached data and its commit SHA without validation, allowing caller
        to decide whether the data is fresh enough. Avoids a GitHub API call for cache hits.
        """
        internal_key, hash_display = await self._get_internal_key(cache_key)

        async with self._lock:
            if internal_key not in self.cache:
                self._cache_misses += 1
                self.logger.debug(
                    "Optimistic cache miss",
                    cache_key=cache_key,
                    hash=hash_display if self._use_hashed_keys else None,
                )
                return None, None

            cached_data = self.cache[internal_key]

            if self._is_entry_expired(cached_data):
                self._cache_expirations += 1
                self._cache_misses += 1
                del self.cache[internal_key]
                self._key_mapping.pop(internal_key, None)
                self._entry_keys.pop(internal_key, None)
                self.logger.info(
                    "Optimistic cache entry expired (TTL)",
                    cache_key=cache_key,
                    hash=hash_display if self._use_hashed_keys else None,
                    ttl_seconds=self._ttl,
                )
                return None, None

            cached_commit_sha = cached_data.get("commit_sha")
            cached_result = cached_data.get("data")

            if cached_result:
                self._cache_hits += 1
                self.cache.move_to_end(internal_key)
                self.logger.info(
                    "Optimistic cache hit",
                    cache_key=cache_key,
                    hash=hash_display if self._use_hashed_keys else None,
                    cached_commit_sha=cached_commit_sha,
                )
                return cast(PRDiff, cached_result), cached_commit_sha
            else:
                self._cache_misses += 1
                self.logger.warning(
                    "Cache entry has no data",
                    cache_key=cache_key,
                    hash=hash_display if self._use_hashed_keys else None,
                )
                return None, None

    async def set(self, cache_key: str, commit_sha: str, data: PRDiff) -> None:
        """Cache PR diff data with associated commit SHA."""
        internal_key, hash_display = await self._get_internal_key(cache_key)

        async with self._lock:
            if internal_key in self.cache:
                del self.cache[internal_key]
            else:
                await self._evict_oldest_if_needed()

            self.cache[internal_key] = {
                "commit_sha": commit_sha,
                "data": data,
                "timestamp": time.time(),
            }
            if self._use_hashed_keys:
                self._entry_keys[internal_key] = cache_key
                if self._store_key_mapping:
                    self._key_mapping[internal_key] = cache_key
        self.logger.info(
            "Cache set",
            cache_key=cache_key,
            hash=hash_display if self._use_hashed_keys else None,
            commit_sha=commit_sha,
        )

    async def invalidate(self, cache_key: str) -> None:
        """Invalidate cache for a specific PR."""
        if self._use_hashed_keys:
            internal_key = self._hash_key(cache_key)
            hash_display = f"{internal_key[:8]}..."
        else:
            internal_key = cache_key
            hash_display = ""

        async with self._lock:
            if internal_key in self.cache:
                del self.cache[internal_key]
                self._key_mapping.pop(internal_key, None)
                self._entry_keys.pop(internal_key, None)
        self.logger.info(
            "Cache invalidated",
            cache_key=cache_key,
            hash=hash_display if self._use_hashed_keys else None,
        )

    async def _invalidate_github_scope(self, owner: str, repo: str, pr_number: int | None) -> None:
        owner_key, repo_key = owner.casefold(), repo.casefold()
        async with self._lock:
            for internal_key in list(self.cache):
                original_key = self._entry_keys.get(internal_key, internal_key)
                strict_parts = original_key.split(":")
                legacy_parts = original_key.split("/")
                if len(strict_parts) == 6 and strict_parts[0] == GITHUB_FULL_DIFF_CACHE_PREFIX_V3:
                    entry_owner, entry_repo, entry_pr = strict_parts[1:4]
                elif len(legacy_parts) == 4 and legacy_parts[2] == "pr":
                    entry_owner, entry_repo, entry_pr = legacy_parts[0], legacy_parts[1], legacy_parts[3]
                else:
                    continue
                if (
                    entry_owner.casefold() == owner_key
                    and entry_repo.casefold() == repo_key
                    and entry_pr.isascii()
                    and entry_pr.isdecimal()
                    and (entry_pr == "0" or entry_pr[0] != "0")
                    and (pr_number is None or entry_pr == str(pr_number))
                ):
                    del self.cache[internal_key]
                    self._key_mapping.pop(internal_key, None)
                    self._entry_keys.pop(internal_key, None)

    async def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
        """Evict all GitHub snapshots and the legacy entry for one PR."""
        await self._invalidate_github_scope(owner, repo, pr_number)

    async def invalidate_github_repository(self, owner: str, repo: str) -> None:
        """Evict GitHub snapshots and legacy entries for one repository."""
        await self._invalidate_github_scope(owner, repo, None)

    async def clear(self) -> None:
        """Clear all cached data and key mappings."""
        async with self._lock:
            self.cache.clear()
            self._key_mapping.clear()
            self._entry_keys.clear()
        self.logger.info("Cache cleared")

    def set_etag(self, cache_key: str, etag: str) -> None:
        """Cache ETag for a specific PR key."""
        cache_entry: dict[str, Any] | None = self.cache.get(cache_key)
        if cache_entry is None:
            cache_entry = {
                "etag": etag,
                "timestamp": time.time(),
            }
            self.cache[cache_key] = cache_entry
        else:
            cache_entry["etag"] = etag
            cache_entry["timestamp"] = time.time()

    def get_etag(self, cache_key: str) -> str | None:
        """Get stored ETag for a cache key."""
        cache_entry = self.cache.get(cache_key)
        if cache_entry is None:
            return None
        return cache_entry.get("etag")

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        base_stats: dict[str, Any] = {
            "cache_size": len(self.cache),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_expirations": self._cache_expirations,
            "cache_evictions_ttl": self._cache_evictions_ttl,
            "cache_evictions_size": self._cache_evictions_size,
        }

        if self._use_hashed_keys:
            if self._store_key_mapping:
                base_stats["keys"] = [self._key_mapping.get(key, key) for key in self.cache.keys()]
            else:
                base_stats["keys"] = list(self.cache.keys())
        else:
            base_stats["keys"] = list(self.cache.keys())

        return base_stats


_cache_service: CacheService | None = None


def get_cache_service() -> CacheService:
    """Get the global cache service instance (singleton pattern)."""
    global _cache_service

    if _cache_service is None:
        _cache_service = CacheService()

    return _cache_service
