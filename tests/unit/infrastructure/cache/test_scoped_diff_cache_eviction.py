"""GitHub-scoped diff cache eviction against the real in-memory cache."""

from dataclasses import asdict
import json
from unittest.mock import Mock, patch

import anyio
import pytest

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.exceptions import ValidationError
from prdiffer.domain.error_codes import E1010_INVALID_CONFIGURATION
from prdiffer.domain.entities.pr_diff_cache import github_full_diff_v3_key, gitlab_full_diff_v1_key
from prdiffer.infrastructure.cache.service import CacheService


@pytest.fixture(params=[(True, True), (True, False), (False, False)], ids=["hashed-mapped", "hashed-unmapped", "plain"])
def cache_service(request: pytest.FixtureRequest) -> CacheService:
    hashed, mapping = request.param
    settings = Mock()
    settings.get.side_effect = lambda key, default: {
        "cache.use_hashed_keys": hashed,
        "cache.store_key_mapping": mapping,
    }.get(key, default)
    with patch("prdiffer.infrastructure.settings.get_settings_service", return_value=settings):
        return CacheService()


def assert_live_metadata(cache_service: CacheService) -> None:
    stats = cache_service.get_stats()
    assert stats["cache_bytes"] == sum(payload_bytes(entry["data"]) for entry in cache_service.cache.values())
    assert stats["cache_bytes"] <= stats["cache_max_bytes"]
    assert cache_service._entry_keys.keys() <= cache_service.cache.keys()
    assert cache_service._key_mapping.keys() <= cache_service.cache.keys()
    if cache_service._use_hashed_keys:
        assert len(cache_service._entry_keys) == sum("data" in entry for entry in cache_service.cache.values())


@pytest.mark.asyncio
async def test_pr_scope_removes_every_snapshot_without_prefix_collisions(cache_service: CacheService) -> None:
    # Given snapshots for one PR alongside similarly named PRs, repos and providers.
    selected = [
        github_full_diff_v3_key("OWNER", "Repo", 12, "base-a", "head-a"),
        github_full_diff_v3_key("owner", "repo", 12, "base-b", "head-b"),
    ]
    retained = [
        github_full_diff_v3_key("owner", "repo", 123, "base", "head"),
        github_full_diff_v3_key("owner", "repo-more", 12, "base", "head"),
        github_full_diff_v3_key("owner-more", "repo", 12, "base", "head"),
        "owner/repo/pr/123",
        "owner/repo-more/pr/12",
        "gitlab:owner/repo/pr/12",
        gitlab_full_diff_v1_key("owner", "repo", 12, 1, "base", "start", "head"),
        "github-full-diff-v30:owner:repo:12:base:head",
    ]
    value = PRDiff(files=())
    for key in selected + retained:
        await cache_service.set(key, "sha", value)

    # When the requested PR is invalidated.
    await cache_service.invalidate_github_pr("OwNeR", "rEpO", 12)

    # Then only its GitHub entries disappear.
    for key in selected:
        assert await cache_service.get(key, "sha") is None
    for key in retained:
        assert await cache_service.get(key, "sha") == value
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_repository_scope_removes_all_prs_only_in_that_repository(cache_service: CacheService) -> None:
    # Given multiple PR identities and an unrelated GitLab MR.
    selected = [github_full_diff_v3_key("owner", "repo", 1, "a", "b"), github_full_diff_v3_key("owner", "repo", 20, "c", "d")]
    retained = [github_full_diff_v3_key("owner", "repository", 1, "a", "b"), "owner/repository/pr/1", "gitlab:owner/repo/pr/1"]
    value = PRDiff(files=())
    for key in selected + retained:
        await cache_service.set(key, "sha", value)

    # When the repository is invalidated.
    await cache_service.invalidate_github_repository("OWNER", "REPO")

    # Then no GitHub entry from that repository survives.
    for key in selected:
        assert await cache_service.get(key, "sha") is None
    for key in retained:
        assert await cache_service.get(key, "sha") == value
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_exact_invalidate_still_removes_only_one_snapshot(cache_service: CacheService) -> None:
    # Given two snapshots for the same PR.
    first = github_full_diff_v3_key("owner", "repo", 1, "base", "head-1")
    second = github_full_diff_v3_key("owner", "repo", 1, "base", "head-2")
    value = PRDiff(files=())
    await cache_service.set(first, "sha", value)
    await cache_service.set(second, "sha", value)

    # When the exact first key is invalidated.
    await cache_service.invalidate(first)

    # Then the second snapshot remains usable.
    assert await cache_service.get(first, "sha") is None
    assert await cache_service.get(second, "sha") == value
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_expiry_and_overwrite_keep_live_original_key_index(cache_service: CacheService) -> None:
    # Given an expired snapshot and an overwritten live snapshot.
    stale = github_full_diff_v3_key("owner", "repo", 1, "base", "old")
    live = github_full_diff_v3_key("owner", "repo", 1, "base", "new")
    value = PRDiff(files=())
    await cache_service.set(stale, "sha", value)
    await cache_service.set(live, "sha", value)
    stale_internal = cache_service._hash_key(stale) if cache_service._use_hashed_keys else stale
    cache_service.cache[stale_internal]["timestamp"] = 0
    await cache_service.set(live, "next-sha", value)

    # When normal lookup expires the stale entry and scoped eviction runs.
    assert await cache_service.get(stale, "sha") is None
    await cache_service.invalidate_github_pr("owner", "repo", 1)

    # Then neither data nor stale metadata remains.
    assert await cache_service.get(live, "next-sha") is None
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_periodic_ttl_sweep_and_lru_remove_reverse_metadata(cache_service: CacheService) -> None:
    # Given a stale entry due for the periodic sweep and a size-limited cache.
    cache_service._cache_max_size = 2
    stale = github_full_diff_v3_key("owner", "repo", 1, "base", "stale")
    live = github_full_diff_v3_key("owner", "repo", 2, "base", "live")
    newest = github_full_diff_v3_key("owner", "repo", 3, "base", "newest")
    value = PRDiff(files=())
    await cache_service.set(stale, "sha", value)
    stale_internal = cache_service._hash_key(stale) if cache_service._use_hashed_keys else stale
    cache_service.cache[stale_internal]["timestamp"] = 0
    cache_service._eviction_call_count = 9

    # When the next insertion sweeps TTL and the following insertion evicts LRU.
    with anyio.fail_after(2):
        await cache_service.set(live, "sha", value)
        await cache_service.set(newest, "sha", value)
        await cache_service.set("elsewhere/repo/pr/4", "sha", value)

    # Then both removal paths preserve valid live metadata and no lock deadlocks.
    assert cache_service._cache_evictions_ttl == 1
    assert cache_service._cache_evictions_size == 1
    assert await cache_service.get(live, "sha") is None
    assert await cache_service.get(newest, "sha") == value
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_get_expiry_removes_live_key_metadata(cache_service: CacheService) -> None:
    # Given a snapshot past its TTL.
    key = github_full_diff_v3_key("owner", "repo", 1, "base", "head")
    await cache_service.set(key, "sha", PRDiff(files=()))
    internal_key = cache_service._hash_key(key) if cache_service._use_hashed_keys else key
    cache_service.cache[internal_key]["timestamp"] = 0

    # When the authoritative lookup expires it.
    result = await cache_service.get(key, "sha")

    # Then the entry and its reverse metadata are gone.
    assert result is None
    assert internal_key not in cache_service.cache
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_clear_removes_live_key_index(cache_service: CacheService) -> None:
    # Given a populated cache with entries for two repositories.
    key = github_full_diff_v3_key("owner", "repo", 1, "base", "head")
    await cache_service.set(key, "sha", PRDiff(files=()))
    await cache_service.set(github_full_diff_v3_key("elsewhere", "repo", 1, "base", "head"), "sha", PRDiff(files=()))

    # When the existing clear API is used.
    await cache_service.clear()

    # Then no live entries or index state remains.
    assert cache_service.cache == {}
    assert cache_service._entry_keys == {}
    assert cache_service._key_mapping == {}
    assert_live_metadata(cache_service)


def payload_bytes(value: PRDiff) -> int:
    return len(json.dumps(asdict(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def unicode_diff(text: str = "é") -> PRDiff:
    return PRDiff(
        files=(
            FileDiffResponse(path="新.py", previous_path="旧.py", status=EDIT_TYPE.RENAMED, stats=FileStats(additions=2, deletions=1), diff=text),
            FileDiffResponse(path="二.py", status=EDIT_TYPE.MODIFIED, stats=FileStats(additions=3, deletions=4), diff="+雪\n"),
        )
    )


@pytest.mark.parametrize("setting", ["cache.max_size", "cache.max_bytes"])
@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_capacity_rejected_at_construction(setting: str, limit: int) -> None:
    # Given an invalid capacity at the configuration boundary.
    settings = Mock()
    settings.get.side_effect = lambda key, default: limit if key == setting else default
    # When the cache is constructed, then its configuration error is structured.
    with patch("prdiffer.infrastructure.settings.get_settings_service", return_value=settings):
        with pytest.raises(ValidationError) as exc:
            CacheService()
    assert exc.value.error_code == E1010_INVALID_CONFIGURATION


@pytest.mark.asyncio
async def test_whole_unicode_payload_fits_exact_byte_budget(cache_service: CacheService) -> None:
    # Given a byte budget including every file, Unicode and rename metadata.
    value = unicode_diff()
    cache_service._cache_max_bytes = payload_bytes(value)
    # When inserted exactly at the budget.
    assert await cache_service.set("exact", "token", value) is None
    # Then the bare value is retained and every payload byte is accounted.
    assert await cache_service.get("exact", "token") is value
    assert cache_service.get_stats()["cache_bytes"] == payload_bytes(value)
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_byte_lru_evicts_multiple_oldest_and_preserves_hit(cache_service: CacheService) -> None:
    # Given three small entries, the oldest of which becomes most recently used.
    small = unicode_diff()
    large = unicode_diff("雪" * 79)
    assert payload_bytes(small) < payload_bytes(large) <= payload_bytes(small) * 2
    cache_service._cache_max_bytes = payload_bytes(small) * 3
    for key in ["a", "b", "c"]:
        await cache_service.set(key, "token", small)
    assert await cache_service.get("a", "token") is small
    # When a large entry requires two byte evictions, below the count limit.
    await cache_service.set("large", "token", large)
    # Then LRU hits are honored and only the necessary entries are evicted.
    assert await cache_service.get("b", "token") is None
    assert await cache_service.get("c", "token") is None
    assert await cache_service.get("a", "token") is small
    assert await cache_service.get("large", "token") is large
    assert cache_service.get_stats()["cache_evictions_size"] == 2
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_one_byte_over_budget_does_not_admit_or_evict(cache_service: CacheService) -> None:
    # Given a budget one byte smaller than the whole Unicode/rename payload.
    value = unicode_diff()
    cache_service._cache_max_bytes = payload_bytes(value) - 1
    empty = PRDiff(files=())
    await cache_service.set("retained", "token", empty)
    # When the otherwise valid new payload exceeds admission by just one byte.
    await cache_service.set("too-large", "token", value)
    # Then admission is all-or-nothing without evicting unrelated data.
    assert await cache_service.get("too-large", "token") is None
    assert await cache_service.get("retained", "token") is empty
    assert cache_service.get_stats()["cache_evictions_size"] == 0
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_larger_replacement_evicts_other_lru_entries(cache_service: CacheService) -> None:
    # Given three equal-sized entries at capacity.
    small, large = unicode_diff(), unicode_diff("雪" * 79)
    cache_service._cache_max_bytes = payload_bytes(small) * 3
    for key in ["oldest", "middle", "replace"]:
        await cache_service.set(key, "old", small)
    # When the newest entry grows, its prior bytes must be released before eviction.
    await cache_service.set("replace", "new", large)
    # Then only the oldest entry is sacrificed, with exact retained accounting.
    assert await cache_service.get("oldest", "old") is None
    assert await cache_service.get("middle", "old") is small
    assert await cache_service.get("replace", "new") is large
    assert cache_service.get_stats()["cache_evictions_size"] == 1
    assert cache_service.get_stats()["cache_bytes"] == payload_bytes(small) + payload_bytes(large)
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
@pytest.mark.parametrize("larger", [True, False])
async def test_replacement_releases_old_weight(cache_service: CacheService, larger: bool) -> None:
    # Given an old value and a differently sized replacement.
    small, large = unicode_diff(), unicode_diff("雪" * 50)
    old, new = (small, large) if larger else (large, small)
    cache_service._cache_max_bytes = payload_bytes(large)
    await cache_service.set("replace", "old", old)
    # When the same key is replaced.
    await cache_service.set("replace", "new", new)
    # Then only the new payload weight remains without a capacity eviction.
    assert cache_service.get_stats()["cache_bytes"] == payload_bytes(new)
    assert cache_service.get_stats()["cache_evictions_size"] == 0
    assert await cache_service.get("replace", "old") is None
    assert await cache_service.get("replace", "new") is new
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_oversized_replacement_removes_only_stale_key(cache_service: CacheService) -> None:
    # Given two small entries and a replacement larger than the whole budget.
    small = unicode_diff()
    cache_service._cache_max_bytes = payload_bytes(small) * 2
    await cache_service.set("stale", "old", small)
    await cache_service.set("other", "token", small)
    # When non-admission replaces a stale value.
    await cache_service.set("stale", "new", unicode_diff("雪" * 1000))
    # Then unrelated entries are intact and stale data and metadata are removed.
    assert await cache_service.get("stale", "old") is None
    assert await cache_service.get("stale", "new") is None
    assert await cache_service.get("other", "token") is small
    assert cache_service.get_stats()["cache_bytes"] == payload_bytes(small)
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_concurrent_mutations_and_synchronous_stats(cache_service: CacheService) -> None:
    # Given a shared cache accessed by independent worker threads/event loops.
    value = unicode_diff()
    cache_service._cache_max_bytes = payload_bytes(value) * 5

    async def exercise(index: int) -> None:
        await cache_service.set(str(index), "token", value)
        await cache_service.get(str(index), "token")
        await cache_service.invalidate(str(index))

    def worker(index: int) -> None:
        anyio.run(exercise, index)
        stats = cache_service.get_stats()
        assert stats["cache_bytes"] == stats["cache_size"] * payload_bytes(value)
        assert len(stats["keys"]) == stats["cache_size"]

    # When concurrent mutations interleave with synchronous health snapshots.
    async with anyio.create_task_group() as group:
        for index in range(30):
            group.start_soon(anyio.to_thread.run_sync, worker, index)
    # Then accounting remains consistent without loop-bound locks.
    assert cache_service.get_stats()["cache_bytes"] == 0
    assert_live_metadata(cache_service)
