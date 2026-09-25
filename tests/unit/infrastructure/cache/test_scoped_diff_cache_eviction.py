"""GitHub-scoped diff cache eviction against the real in-memory cache."""

from unittest.mock import Mock, patch

import anyio
import pytest

from prdiffer.domain.entities.pr_diff import PRDiff
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
    assert cache_service._entry_keys.keys() <= cache_service.cache.keys()
    assert cache_service._key_mapping.keys() <= cache_service.cache.keys()
    if cache_service._use_hashed_keys:
        assert len(cache_service._entry_keys) == sum("data" in entry for entry in cache_service.cache.values())


@pytest.mark.asyncio
async def test_pr_scope_removes_every_snapshot_and_legacy_without_prefix_collisions(cache_service: CacheService) -> None:
    # Given snapshots for one PR alongside similarly named PRs, repos and providers.
    selected = [
        github_full_diff_v3_key("OWNER", "Repo", 12, "base-a", "head-a"),
        github_full_diff_v3_key("owner", "repo", 12, "base-b", "head-b"),
        "Owner/Repo/pr/12",
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
    selected = [github_full_diff_v3_key("owner", "repo", 1, "a", "b"), github_full_diff_v3_key("owner", "repo", 20, "c", "d"), "OWNER/REPO/pr/1"]
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
async def test_optimistic_expiry_removes_live_key_metadata(cache_service: CacheService) -> None:
    # Given a snapshot past its TTL.
    key = github_full_diff_v3_key("owner", "repo", 1, "base", "head")
    await cache_service.set(key, "sha", PRDiff(files=()))
    internal_key = cache_service._hash_key(key) if cache_service._use_hashed_keys else key
    cache_service.cache[internal_key]["timestamp"] = 0

    # When the optimistic lookup expires it.
    result = await cache_service.get_optimistic(key)

    # Then the entry and its reverse metadata are gone.
    assert result == (None, None)
    assert internal_key not in cache_service.cache
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_scoped_eviction_handles_etag_entries_without_harming_other_etags(cache_service: CacheService) -> None:
    # Given raw ETag entries coexisting with hashed diff data.
    selected = github_full_diff_v3_key("owner", "repo", 8, "base", "head")
    unrelated = "gitlab:owner/repo/pr/8"
    value = PRDiff(files=())
    await cache_service.set(selected, "sha", value)
    cache_service.set_etag(selected, "selected-etag")
    cache_service.set_etag(unrelated, "gitlab-etag")

    # When the matching PR is evicted.
    await cache_service.invalidate_github_pr("owner", "repo", 8)

    # Then its data and ETag disappear, but the GitLab ETag survives.
    assert await cache_service.get(selected, "sha") is None
    assert cache_service.get_etag(selected) is None
    assert cache_service.get_etag(unrelated) == "gitlab-etag"
    assert_live_metadata(cache_service)


@pytest.mark.asyncio
async def test_clear_removes_live_key_index(cache_service: CacheService) -> None:
    # Given a populated cache with an ETag.
    key = github_full_diff_v3_key("owner", "repo", 1, "base", "head")
    await cache_service.set(key, "sha", PRDiff(files=()))
    cache_service.set_etag("elsewhere/repo/pr/1", "etag")

    # When the existing clear API is used.
    await cache_service.clear()

    # Then no live entries or index state remains.
    assert cache_service.cache == {}
    assert cache_service._entry_keys == {}
    assert cache_service._key_mapping == {}
