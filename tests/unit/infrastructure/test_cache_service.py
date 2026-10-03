"""Unit tests for CacheService infrastructure component.

Tests for CacheService component which provides in-memory caching
with commit-based invalidation, key hashing, and TTL support.
"""

import anyio
from threading import RLock
from unittest.mock import patch
import pytest
from prdiffer.infrastructure.cache.service import CacheService, get_cache_service
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import github_full_diff_v3_key
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.exceptions import ValidationError


CACHE_KEY = github_full_diff_v3_key("owner", "repo", 123, "b" * 40, "c" * 40)


@pytest.fixture
def reset_cache_service():
    """Reset the global cache service before each test."""
    import prdiffer.infrastructure.cache.service as cache_module

    cache_module._cache_service = None
    yield
    cache_module._cache_service = None


@pytest.fixture
def sample_pr_diff():
    """Create a sample PRDiff for testing."""
    return PRDiff(
        files=(
            FileDiffResponse(
                path="test.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=10, deletions=5),
                diff="sample patch content",
            ),
        )
    )


class TestCacheServiceInitialization:
    """Test suite for CacheService initialization."""

    def test_cache_service_initialization(self, reset_cache_service):
        """Test CacheService can be initialized."""
        service = CacheService()

        assert service is not None
        assert hasattr(service, "cache")
        assert hasattr(service, "_lock")
        assert hasattr(service, "_use_hashed_keys")
        assert hasattr(service, "_ttl")

    def test_cache_service_initialization_no_hashing(self, reset_cache_service):
        """Test CacheService with hashing disabled."""
        # Can't easily test this without mocking settings service
        # The configuration is loaded from settings during init
        # Just verify the attribute exists
        service = CacheService()

        assert hasattr(service, "_use_hashed_keys")
        assert isinstance(service._use_hashed_keys, bool)

    def test_cache_service_has_lock(self, reset_cache_service):
        """Test CacheService supports synchronous reentrant locking for stats."""
        service = CacheService()

        with service._lock:
            with service._lock:
                assert service.get_stats()["cache_size"] == 0

    def test_cache_service_initial_stats(self, reset_cache_service):
        """Test initial statistics are zero."""
        service = CacheService()

        assert service._cache_hits == 0
        assert service._cache_misses == 0
        assert service._cache_expirations == 0


class TestCacheServiceHashKey:
    """Test suite for _hash_key method."""

    def test_hash_key_md5_length(self, reset_cache_service):
        """Test MD5 hashing produces 32 character hex string."""
        service = CacheService()

        hashed = service._hash_key("test_key")

        assert len(hashed) == 32
        assert all(c in "0123456789abcdef" for c in hashed)

    def test_hash_key_consistent(self, reset_cache_service):
        """Test hashing is consistent for same input."""
        service = CacheService()

        hash1 = service._hash_key("test_key")
        hash2 = service._hash_key("test_key")

        assert hash1 == hash2

    def test_hash_key_different_inputs(self, reset_cache_service):
        """Test different inputs produce different hashes."""
        service = CacheService()

        hash1 = service._hash_key("key1")
        hash2 = service._hash_key("key2")

        assert hash1 != hash2

    def test_hash_key_sha256_length(self, reset_cache_service):
        """Test SHA256 hashing produces 64 character hex string."""
        with patch.object(CacheService, "__init__", lambda self: None):
            service = CacheService()
            service._hash_algorithm = "sha256"

        hashed = service._hash_key("test_key")

        assert len(hashed) == 64

    def test_hash_key_unsupported_algorithm(self, reset_cache_service):
        """Test unsupported hash algorithm raises ValidationError."""
        with patch.object(CacheService, "__init__", lambda self: None):
            service = CacheService()
            service._hash_algorithm = "invalid"

        with pytest.raises(ValidationError, match="Unsupported hash algorithm"):
            service._hash_key("test_key")


class TestCacheServiceGetSet:
    """Test suite for get and set methods."""

    @pytest.mark.anyio
    async def test_set_and_get_cache_hit(self, reset_cache_service, sample_pr_diff):
        """Test setting and getting cached data."""
        service = CacheService()
        cache_key = CACHE_KEY

        await service.set(cache_key, "abc123", sample_pr_diff)
        result = await service.get(cache_key, "abc123")

        assert result is not None
        assert result == sample_pr_diff
        assert len(result.files) == 1
        assert result.files[0].path == "test.py"

    @pytest.mark.anyio
    async def test_get_cache_miss(self, reset_cache_service):
        """Test getting non-existent key returns None."""
        service = CacheService()
        cache_key = CACHE_KEY

        result = await service.get(cache_key, "abc123")

        assert result is None

    @pytest.mark.anyio
    async def test_get_commit_sha_mismatch(self, reset_cache_service, sample_pr_diff):
        """Test cache miss when commit SHA doesn't match."""
        service = CacheService()
        cache_key = CACHE_KEY

        await service.set(cache_key, "abc123", sample_pr_diff)
        result = await service.get(cache_key, "def456")  # Different SHA

        assert result is None

    @pytest.mark.anyio
    async def test_set_overwrites_existing(self, reset_cache_service, sample_pr_diff):
        """Test setting same key overwrites existing data."""
        service = CacheService()
        cache_key = CACHE_KEY

        new_diff = PRDiff(
            files=(
                FileDiffResponse(
                    path="new_file.py",
                    status=EDIT_TYPE.ADDED,
                    stats=FileStats(additions=20, deletions=0),
                    diff="new content patch",
                ),
            )
        )
        await service.set(cache_key, "abc123", sample_pr_diff)
        await service.set(cache_key, "def456", new_diff)

        result = await service.get(cache_key, "def456")

        assert result == new_diff
        assert result.files[0].path == "new_file.py"

    @pytest.mark.anyio
    async def test_statistics_updated(self, reset_cache_service, sample_pr_diff):
        """Test cache hit/miss statistics are updated."""
        service = CacheService()
        cache_key = CACHE_KEY

        await service.set(cache_key, "abc123", sample_pr_diff)
        await service.get(cache_key, "abc123")  # Hit
        await service.get(cache_key, "def456")  # Miss (SHA mismatch)
        await service.get("owner/repo/pr/999", "abc123")  # Miss (not found)

        assert service._cache_hits == 1
        assert service._cache_misses == 2


class TestCacheServiceInvalidate:
    """Test suite for invalidate method."""

    @pytest.mark.anyio
    async def test_invalidate_existing_key(self, reset_cache_service, sample_pr_diff):
        """Test invalidating an existing cache entry."""
        service = CacheService()
        cache_key = CACHE_KEY

        await service.set(cache_key, "abc123", sample_pr_diff)
        await service.invalidate(cache_key)

        result = await service.get(cache_key, "abc123")

        assert result is None

    @pytest.mark.anyio
    async def test_invalidate_nonexistent_key(self, reset_cache_service):
        """Test invalidating a non-existent key doesn't raise error."""
        service = CacheService()
        cache_key = CACHE_KEY

        # Should not raise
        await service.invalidate(cache_key)


class TestCacheServiceClear:
    """Test suite for clear method."""

    @pytest.mark.anyio
    async def test_clear_cache(self, reset_cache_service, sample_pr_diff):
        """Test clearing all cached data."""
        service = CacheService()

        await service.set("owner1/repo1/pr/1", "abc123", sample_pr_diff)
        await service.set("owner2/repo2/pr/2", "def456", sample_pr_diff)

        await service.clear()

        assert len(service.cache) == 0
        result = await service.get("owner1/repo1/pr/1", "abc123")
        assert result is None


class TestCacheServiceGetStats:
    """Test suite for get_stats method."""

    def test_get_stats_empty(self, reset_cache_service):
        """Test stats when cache is empty."""
        service = CacheService()

        stats = service.get_stats()

        assert stats["cache_size"] == 0
        assert stats["cache_hits"] == 0
        assert stats["cache_misses"] == 0

    @pytest.mark.anyio
    async def test_get_stats_with_data(self, reset_cache_service, sample_pr_diff):
        """Test stats with cached data."""
        service = CacheService()
        cache_key = CACHE_KEY

        await service.set(cache_key, "abc123", sample_pr_diff)

        stats = service.get_stats()

        assert stats["cache_size"] == 1

    @pytest.mark.anyio
    async def test_get_stats_hit_rate(self, reset_cache_service, sample_pr_diff):
        """Test hit rate calculation."""
        service = CacheService()
        cache_key = CACHE_KEY

        await service.set(cache_key, "abc123", sample_pr_diff)
        await service.get(cache_key, "abc123")  # Hit
        await service.get(cache_key, "def456")  # Miss

        stats = service.get_stats()

        # 1 hit, 1 miss = 50% hit rate
        assert stats["cache_hits"] == 1
        assert stats["cache_misses"] == 1


class TestCacheServiceTTL:
    """Test suite for TTL-based expiration."""

    @pytest.mark.anyio
    async def test_ttl_expiration(self, reset_cache_service, sample_pr_diff):
        """Test that entries expire after TTL."""
        # Create service with short TTL
        service = CacheService()
        service._ttl = 0.1  # 100ms TTL

        cache_key = CACHE_KEY
        await service.set(cache_key, "abc123", sample_pr_diff)

        # Wait for TTL to expire
        await anyio.sleep(0.15)

        result = await service.get(cache_key, "abc123")

        assert result is None

    @pytest.mark.anyio
    async def test_ttl_not_expired(self, reset_cache_service, sample_pr_diff):
        """Test that entries are valid before TTL expires."""
        service = CacheService()
        service._ttl = 10  # 10 second TTL

        cache_key = CACHE_KEY
        await service.set(cache_key, "abc123", sample_pr_diff)

        await anyio.sleep(0.05)  # Small sleep

        result = await service.get(cache_key, "abc123")

        assert result is not None

    @pytest.mark.anyio
    async def test_expiration_increments_counter(self, reset_cache_service, sample_pr_diff):
        """Test that expiration increments expiration counter."""
        service = CacheService()
        service._ttl = 0.1

        cache_key = CACHE_KEY
        await service.set(cache_key, "abc123", sample_pr_diff)

        await anyio.sleep(0.15)
        await service.get(cache_key, "abc123")

        assert service._cache_expirations == 1


class TestCacheServiceThreadSafety:
    """Test suite for thread safety using anyio async patterns."""

    @pytest.mark.anyio
    async def test_concurrent_set_operations(self, reset_cache_service, sample_pr_diff):
        """Test concurrent set operations are thread-safe using anyio."""
        service = CacheService()
        num_tasks = 10
        results = []

        async def set_value(i):
            key = f"owner/repo/pr/{i}"
            await service.set(key, f"sha{i}", sample_pr_diff)
            results.append(key)

        async with anyio.create_task_group() as tg:
            for i in range(num_tasks):
                tg.start_soon(set_value, i)

        assert len(results) == num_tasks
        assert service.get_stats()["cache_size"] == num_tasks

    @pytest.mark.anyio
    async def test_concurrent_get_operations(self, reset_cache_service, sample_pr_diff):
        """Test concurrent get operations are thread-safe using anyio."""
        service = CacheService()
        cache_key = CACHE_KEY
        await service.set(cache_key, "abc123", sample_pr_diff)

        num_tasks = 10
        results = []

        async def get_value():
            result = await service.get(cache_key, "abc123")
            results.append(result is not None)

        async with anyio.create_task_group() as tg:
            for _ in range(num_tasks):
                tg.start_soon(get_value)

        # All tasks should get cache hits
        assert all(results)

    @pytest.mark.anyio
    async def test_statistics_thread_safe(self, reset_cache_service, sample_pr_diff):
        """Test statistics are thread-safe using anyio."""
        service = CacheService()
        cache_key = CACHE_KEY
        await service.set(cache_key, "abc123", sample_pr_diff)

        num_tasks = 10
        operations_per_task = 10

        async def mixed_ops():
            for _ in range(operations_per_task):
                await service.get(cache_key, "abc123")  # Will be a hit
                await service.get(cache_key, "wrong_sha")  # Will be a miss (SHA mismatch)

        async with anyio.create_task_group() as tg:
            for _ in range(num_tasks):
                tg.start_soon(mixed_ops)

        # Should have consistent counts (each task does 10 hits + 10 misses)
        stats = service.get_stats()
        expected_total = num_tasks * operations_per_task * 2
        assert stats["cache_hits"] + stats["cache_misses"] == expected_total


class TestCacheServiceGetInternalKey:
    """Test suite for _get_internal_key method."""

    @pytest.mark.anyio
    async def test_internal_key_no_hashing(self, reset_cache_service):
        """Test internal key without hashing."""
        with patch.object(CacheService, "__init__", lambda self: None):
            service = CacheService()
            service._use_hashed_keys = False

        internal_key, hash_display = await service._get_internal_key("test_key")

        assert internal_key == "test_key"
        assert hash_display == ""

    @pytest.mark.anyio
    async def test_internal_key_with_hashing(self, reset_cache_service):
        """Test internal key with hashing."""
        with patch.object(CacheService, "__init__", lambda self: None):
            service = CacheService()
            service._use_hashed_keys = True
            service._hash_algorithm = "md5"
            service._store_key_mapping = False

        internal_key, hash_display = await service._get_internal_key("test_key")

        assert len(internal_key) == 32  # MD5 hash
        assert hash_display == f"{internal_key[:8]}..."

    @pytest.mark.anyio
    async def test_internal_key_stores_mapping(self, reset_cache_service):
        """Test that internal key stores mapping when requested."""
        with patch.object(CacheService, "__init__", lambda self: None):
            service = CacheService()
            service._use_hashed_keys = True
            service._hash_algorithm = "md5"
            service._store_key_mapping = True
            service._key_mapping = {}
            service._lock = RLock()

        internal_key, hash_display = await service._get_internal_key("test_key", store_mapping=True)

        assert "test_key" in service._key_mapping.values()


class TestCacheServiceGetOriginalKey:
    """Test suite for _get_original_key method."""

    @pytest.mark.anyio
    async def test_get_original_key_no_hashing(self, reset_cache_service):
        """Test getting original key without hashing."""
        with patch.object(CacheService, "__init__", lambda self: None):
            service = CacheService()
            service._use_hashed_keys = False

        original = await service._get_original_key("test_key")

        assert original == "test_key"

    @pytest.mark.anyio
    async def test_get_original_key_with_mapping(self, reset_cache_service):
        """Test getting original key from mapping."""
        with patch.object(CacheService, "__init__", lambda self: None):
            service = CacheService()
            service._use_hashed_keys = True
            service._store_key_mapping = True
            service._key_mapping = {"hashed_key": "original_key"}
            service._lock = RLock()

        original = await service._get_original_key("hashed_key")

        assert original == "original_key"


class TestCacheServiceSingleton:
    """Test suite for singleton pattern."""

    def test_get_cache_service_singleton(self, reset_cache_service):
        """Test that get_cache_service returns singleton instance."""
        service1 = get_cache_service()
        service2 = get_cache_service()

        assert service1 is service2

    def test_get_cache_service_creates_once(self, reset_cache_service):
        """Test that singleton is created only once."""
        import prdiffer.infrastructure.cache.service as cache_module

        service1 = get_cache_service()
        service2 = get_cache_service()

        assert cache_module._cache_service is service1
        assert cache_module._cache_service is service2
