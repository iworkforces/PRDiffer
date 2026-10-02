"""Tests for cache key hashing functionality in CacheService.

This module tests the MD5-based cache key hashing implementation,
including configuration, reverse mapping, and logging behavior.
"""

import hashlib
import pytest
from unittest.mock import Mock, patch
from prdiffer.infrastructure.cache.service import CacheService
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.exceptions import ValidationError


@pytest.fixture
def mock_settings_hashing_enabled():
    """Mock settings service with hashing enabled."""
    mock_settings = Mock()
    mock_settings.get.side_effect = lambda key, default: {
        "cache.use_hashed_keys": True,
        "cache.hash_algorithm": "md5",
        "cache.store_key_mapping": True,
    }.get(key, default)
    return mock_settings


@pytest.fixture
def mock_settings_hashing_disabled():
    """Mock settings service with hashing disabled."""
    mock_settings = Mock()
    mock_settings.get.side_effect = lambda key, default: {
        "cache.use_hashed_keys": False,
        "cache.hash_algorithm": "md5",
        "cache.store_key_mapping": False,
    }.get(key, default)
    return mock_settings


@pytest.fixture
def mock_settings_no_mapping():
    """Mock settings service with hashing enabled but no reverse mapping."""
    mock_settings = Mock()
    mock_settings.get.side_effect = lambda key, default: {
        "cache.use_hashed_keys": True,
        "cache.hash_algorithm": "md5",
        "cache.store_key_mapping": False,
    }.get(key, default)
    return mock_settings


@pytest.fixture
def sample_pr_diff():
    """Sample PRDiff object for testing."""
    return PRDiff(files=())


class TestCacheKeyHashing:
    """Test cache key hashing functionality."""

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    def test_hash_key_md5(self, mock_get_settings, mock_settings_hashing_enabled):
        """Test MD5 hash generation."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        key = "karcher-digital/iotc-device-management/pr/163"
        expected_hash = hashlib.md5(key.encode("utf-8")).hexdigest()
        actual_hash = cache_service._hash_key(key)

        assert actual_hash == expected_hash
        assert len(actual_hash) == 32  # MD5 produces 32 hex chars

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    def test_hash_key_sha256(self, mock_get_settings):
        """Test SHA-256 hash generation."""
        mock_settings = Mock()
        mock_settings.get.side_effect = lambda key, default: {
            "cache.use_hashed_keys": True,
            "cache.hash_algorithm": "sha256",
            "cache.store_key_mapping": True,
        }.get(key, default)
        mock_get_settings.return_value = mock_settings

        cache_service = CacheService()
        key = "test/repo/pr/123"
        expected_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        actual_hash = cache_service._hash_key(key)

        assert actual_hash == expected_hash
        assert len(actual_hash) == 64  # SHA-256 produces 64 hex chars

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    def test_hash_key_unsupported_algorithm(self, mock_get_settings, mock_settings_hashing_enabled):
        """Test error handling for unsupported hash algorithm."""
        mock_settings = Mock()
        mock_settings.get.side_effect = lambda key, default: {
            "cache.use_hashed_keys": True,
            "cache.hash_algorithm": "invalid",
            "cache.store_key_mapping": True,
        }.get(key, default)
        mock_get_settings.return_value = mock_settings

        cache_service = CacheService()

        with pytest.raises(ValidationError, match="Unsupported hash algorithm"):
            cache_service._hash_key("test/repo/pr/123")

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_get_internal_key_with_hashing(self, mock_get_settings, mock_settings_hashing_enabled):
        """Test internal key generation with hashing enabled."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        original_key = "owner/repo/pr/123"
        internal_key, hash_display = await cache_service._get_internal_key(original_key)

        # Internal key should be the hash
        expected_hash = hashlib.md5(original_key.encode("utf-8")).hexdigest()
        assert internal_key == expected_hash

        # Hash display should be short prefix with ellipsis
        assert hash_display == f"{expected_hash[:8]}..."
        assert len(hash_display) == 11  # 8 chars + "..."

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_get_internal_key_without_hashing(self, mock_get_settings, mock_settings_hashing_disabled):
        """Test internal key generation with hashing disabled."""
        mock_get_settings.return_value = mock_settings_hashing_disabled
        cache_service = CacheService()

        original_key = "owner/repo/pr/123"
        internal_key, hash_display = await cache_service._get_internal_key(original_key)

        # Internal key should be the original key
        assert internal_key == original_key
        # Hash display should be empty
        assert hash_display == ""

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_reverse_mapping_stored(self, mock_get_settings, mock_settings_hashing_enabled):
        """Test that reverse mapping is stored when configured."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        original_key = "owner/repo/pr/123"
        internal_key, _ = await cache_service._get_internal_key(original_key, store_mapping=True)

        # Reverse mapping should be stored
        assert internal_key in cache_service._key_mapping
        assert cache_service._key_mapping[internal_key] == original_key

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_reverse_mapping_not_stored(self, mock_get_settings, mock_settings_no_mapping):
        """Test that reverse mapping is not stored when disabled."""
        mock_get_settings.return_value = mock_settings_no_mapping
        cache_service = CacheService()

        original_key = "owner/repo/pr/123"
        await cache_service._get_internal_key(original_key)

        # Reverse mapping should be empty
        assert len(cache_service._key_mapping) == 0

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_get_original_key_with_mapping(self, mock_get_settings, mock_settings_hashing_enabled):
        """Test retrieving original key from hash using mapping."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        original_key = "owner/repo/pr/123"
        internal_key, _ = await cache_service._get_internal_key(original_key, store_mapping=True)

        # Get original key back from hash
        retrieved_key = await cache_service._get_original_key(internal_key)
        assert retrieved_key == original_key

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_get_original_key_without_mapping(self, mock_get_settings, mock_settings_no_mapping):
        """Test getting original key when mapping is disabled."""
        mock_get_settings.return_value = mock_settings_no_mapping
        cache_service = CacheService()

        hashed_key = "abc123def456"
        retrieved_key = await cache_service._get_original_key(hashed_key)

        # Should return the hash itself since no mapping exists
        assert retrieved_key == hashed_key


class TestCacheOperationsWithHashing:
    """Test cache operations (get, set, invalidate) with hashing."""

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_set_and_get_with_hashing(self, mock_get_settings, mock_settings_hashing_enabled, sample_pr_diff):
        """Test setting and getting cache with hashed keys."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        cache_key = "owner/repo/pr/123"
        commit_sha = "abc123"

        # Set cache
        await cache_service.set(cache_key, commit_sha, sample_pr_diff)

        # Verify data is stored under hashed key
        expected_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()
        assert expected_hash in cache_service.cache
        assert expected_hash not in ["owner/repo/pr/123"]  # Original key not used

        # Get cache
        retrieved = await cache_service.get(cache_key, commit_sha)
        assert retrieved == sample_pr_diff

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_set_and_get_without_hashing(self, mock_get_settings, mock_settings_hashing_disabled, sample_pr_diff):
        """Test setting and getting cache without hashing."""
        mock_get_settings.return_value = mock_settings_hashing_disabled
        cache_service = CacheService()

        cache_key = "owner/repo/pr/123"
        commit_sha = "abc123"

        # Set cache
        await cache_service.set(cache_key, commit_sha, sample_pr_diff)

        # Verify data is stored under original key
        assert cache_key in cache_service.cache

        # Get cache
        retrieved = await cache_service.get(cache_key, commit_sha)
        assert retrieved == sample_pr_diff

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_cache_miss_with_hashing(self, mock_get_settings, mock_settings_hashing_enabled):
        """Test cache miss with hashing enabled."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        cache_key = "owner/repo/pr/999"
        commit_sha = "xyz789"

        # Cache miss should return None
        retrieved = await cache_service.get(cache_key, commit_sha)
        assert retrieved is None

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_invalidate_with_hashing(self, mock_get_settings, mock_settings_hashing_enabled, sample_pr_diff):
        """Test cache invalidation with hashing."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        cache_key = "owner/repo/pr/123"
        commit_sha = "abc123"

        # Set and verify
        await cache_service.set(cache_key, commit_sha, sample_pr_diff)
        assert await cache_service.get(cache_key, commit_sha) == sample_pr_diff

        # Invalidate
        await cache_service.invalidate(cache_key)

        # Verify removed
        assert await cache_service.get(cache_key, commit_sha) is None

        # Verify reverse mapping also removed
        expected_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()
        assert expected_hash not in cache_service._key_mapping

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_clear_with_hashing(self, mock_get_settings, mock_settings_hashing_enabled, sample_pr_diff):
        """Test clearing cache with hashing."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        # Add multiple entries
        for i in range(3):
            cache_key = f"owner/repo/pr/{i}"
            await cache_service.set(cache_key, f"sha{i}", sample_pr_diff)

        # Verify entries and mappings exist
        assert len(cache_service.cache) == 3
        assert len(cache_service._key_mapping) == 3

        # Clear
        await cache_service.clear()

        # Verify everything cleared
        assert len(cache_service.cache) == 0
        assert len(cache_service._key_mapping) == 0

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_commit_sha_mismatch_with_hashing(self, mock_get_settings, mock_settings_hashing_enabled, sample_pr_diff):
        """Test cache invalidation when commit SHA changes."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        cache_key = "owner/repo/pr/123"
        old_commit = "abc123"
        new_commit = "xyz789"

        # Set with old commit
        await cache_service.set(cache_key, old_commit, sample_pr_diff)

        # Try to get with new commit (should be cache miss)
        retrieved = await cache_service.get(cache_key, new_commit)
        assert retrieved is None


class TestCacheStatsWithHashing:
    """Test cache statistics with hashing."""

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_stats_with_hashing_and_mapping(self, mock_get_settings, mock_settings_hashing_enabled, sample_pr_diff):
        """Test that stats return original keys when mapping is enabled."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        # Add entries
        keys = [
            "owner1/repo1/pr/1",
            "owner2/repo2/pr/2",
            "karcher-digital/iotc-device-management/pr/163",
        ]
        for key in keys:
            await cache_service.set(key, "sha", sample_pr_diff)

        # Get stats
        stats = cache_service.get_stats()

        # Should return original keys, not hashes
        assert stats["cache_size"] == 3
        assert set(stats["keys"]) == set(keys)  # Original keys returned

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_stats_with_hashing_no_mapping(self, mock_get_settings, mock_settings_no_mapping, sample_pr_diff):
        """Test that stats return hashed keys when mapping is disabled."""
        mock_get_settings.return_value = mock_settings_no_mapping
        cache_service = CacheService()

        # Add entry
        cache_key = "owner/repo/pr/123"
        await cache_service.set(cache_key, "sha", sample_pr_diff)

        # Get stats
        stats = cache_service.get_stats()

        # Should return hashed keys since no mapping
        assert stats["cache_size"] == 1
        expected_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()
        assert stats["keys"] == [expected_hash]

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_stats_without_hashing(self, mock_get_settings, mock_settings_hashing_disabled, sample_pr_diff):
        """Test stats without hashing enabled."""
        mock_get_settings.return_value = mock_settings_hashing_disabled
        cache_service = CacheService()

        # Add entry
        cache_key = "owner/repo/pr/123"
        await cache_service.set(cache_key, "sha", sample_pr_diff)

        # Get stats
        stats = cache_service.get_stats()

        # Should return original keys
        assert stats["cache_size"] == 1
        assert stats["keys"] == [cache_key]


class TestCacheKeyConsistency:
    """Test consistency of cache key generation and hashing."""

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    def test_same_key_produces_same_hash(self, mock_get_settings, mock_settings_hashing_enabled):
        """Test that the same key always produces the same hash."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        key = "owner/repo/pr/123"
        hash1 = cache_service._hash_key(key)
        hash2 = cache_service._hash_key(key)

        assert hash1 == hash2

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    def test_different_keys_produce_different_hashes(self, mock_get_settings, mock_settings_hashing_enabled):
        """Test that different keys produce different hashes."""
        mock_get_settings.return_value = mock_settings_hashing_enabled
        cache_service = CacheService()

        key1 = "owner1/repo1/pr/123"
        key2 = "owner2/repo2/pr/456"

        hash1 = cache_service._hash_key(key1)
        hash2 = cache_service._hash_key(key2)

        assert hash1 != hash2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
