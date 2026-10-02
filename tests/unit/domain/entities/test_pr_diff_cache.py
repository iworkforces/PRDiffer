"""Tests for strict PRDiff cache identity (GitHub v3 + GitLab v1)."""

from __future__ import annotations

import pytest

from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import (
    GITHUB_FULL_DIFF_CACHE_PREFIX,
    PRDIFF_CACHE_SCHEMA_V2,
    StrictPRDiffCacheIdentity,
    github_full_diff_v3_identity,
    github_full_diff_v3_key,
    unwrap_pr_diff_cache_value,
)

MB = "b" * 40
HD = "c" * 40
MB2 = "e" * 40


def _diff() -> PRDiff:
    return PRDiff(
        files=(
            FileDiffResponse(
                path="a.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=1, deletions=0),
                diff="+x",
            ),
        )
    )


def test_strict_identity_is_frozen() -> None:
    identity = StrictPRDiffCacheIdentity(
        cache_key="k",
        validation_token="t",
        schema_version=2,
    )
    with pytest.raises(Exception):
        identity.validation_token = "x"  # type: ignore[misc]
    with pytest.raises(Exception):
        identity.schema_version = 1  # type: ignore[misc]


def test_github_v3_identity_includes_merge_base_and_head() -> None:
    identity = github_full_diff_v3_identity("Owner", "Repo", 7, MB, HD)
    expected_key = github_full_diff_v3_key("Owner", "Repo", 7, MB, HD)
    assert identity.cache_key == expected_key
    assert identity.cache_key == f"{GITHUB_FULL_DIFF_CACHE_PREFIX}:owner:repo:7:{MB}:{HD}"
    assert identity.validation_token == f"{MB}:{HD}"
    assert identity.schema_version == PRDIFF_CACHE_SCHEMA_V2


def test_github_v3_distinct_merge_base_same_head() -> None:
    a = github_full_diff_v3_identity("o", "r", 1, MB, HD)
    b = github_full_diff_v3_identity("o", "r", 1, MB2, HD)
    assert a.cache_key != b.cache_key
    assert a.validation_token != b.validation_token


def test_unwrap_bare_value_under_v3_key() -> None:
    value = _diff()
    key = github_full_diff_v3_key("o", "r", 1, MB, HD)
    identity = github_full_diff_v3_identity("o", "r", 1, MB, HD)
    assert unwrap_pr_diff_cache_value(value, key=key) is value
    assert unwrap_pr_diff_cache_value(value, key=key, identity=identity) is value
    assert unwrap_pr_diff_cache_value(value, key="owner/repo/pr/1") is None
    assert unwrap_pr_diff_cache_value(value, key="not-a-strict-key:o:r:1:h") is None


def test_unwrap_ignores_non_prdiff_values() -> None:
    key = github_full_diff_v3_key("o", "r", 1, MB, HD)
    assert unwrap_pr_diff_cache_value(object(), key=key) is None


def test_unwrap_requires_exact_identity_key() -> None:
    value = _diff()
    identity = github_full_diff_v3_identity("o", "r", 1, MB, HD)
    assert unwrap_pr_diff_cache_value(value, key="", identity=identity) is None
    assert unwrap_pr_diff_cache_value(value, key=f"ns:{identity.cache_key}", identity=identity) is None


def test_unwrap_rejects_literal_github_v2_key_under_v3_identity() -> None:
    """Legacy github-full-diff-v2 keys must miss for an active v3 identity."""
    value = _diff()
    identity = github_full_diff_v3_identity("o", "r", 1, MB, HD)
    v2_key = f"github-full-diff-v2:o:r:1:{MB}:{HD}"
    assert unwrap_pr_diff_cache_value(value, key=v2_key, identity=identity) is None
    # Bare v2-shaped key without identity also misses (not a v3 prefix).
    assert unwrap_pr_diff_cache_value(value, key=v2_key) is None


def test_github_v2_key_builder_apis_removed() -> None:
    """Hard cutover: no v2 key/token builders remain in the cache module."""
    import prdiffer.domain.entities.pr_diff_cache as cache_mod

    assert not hasattr(cache_mod, "github_full_diff_v2_key")
    assert not hasattr(cache_mod, "github_full_diff_v2_identity")
    assert not hasattr(cache_mod, "github_full_diff_v2_validation_token")
    assert not hasattr(cache_mod, "GITHUB_FULL_DIFF_CACHE_PREFIX_V2")
    assert cache_mod.GITHUB_FULL_DIFF_CACHE_PREFIX == "github-full-diff-v3"
