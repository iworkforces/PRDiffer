"""Tests for GitLab strict full-diff v1 cache identity builders."""

from __future__ import annotations

import pytest

from prdiffer.domain.entities.pr_diff_cache import (
    GITLAB_FULL_DIFF_CACHE_PREFIX,
    PRDIFF_CACHE_SCHEMA_V1,
    StrictPRDiffCacheIdentity,
    gitlab_full_diff_v1_identity,
    gitlab_full_diff_v1_key,
    gitlab_full_diff_v1_validation_token,
    unwrap_pr_diff_cache_value,
)
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff


def _diff() -> PRDiff:
    return PRDiff(
        head_sha="c" * 40,
        files=(
            FileDiffResponse(
                path="a.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=1, deletions=0),
                diff="+x",
            ),
        ),
    )


def test_gitlab_v1_key_exact_format_and_casefold() -> None:
    key = gitlab_full_diff_v1_key(
        namespace="Group/SubGroup",
        repo="Project",
        iid=42,
        version_id=99,
        base_sha="baseSHA",
        start_sha="startSHA",
        head_sha="headSHA",
    )
    assert key == (f"{GITLAB_FULL_DIFF_CACHE_PREFIX}:gitlab.com:group/subgroup:project:42:99:baseSHA:startSHA:headSHA")


def test_gitlab_v1_key_includes_custom_host() -> None:
    key = gitlab_full_diff_v1_key(
        namespace="trace-analysis",
        repo="oh-my-grokbuild",
        iid=1,
        version_id=3,
        base_sha="b",
        start_sha="s",
        head_sha="h",
        host="gitlab.example.com",
    )
    assert key == (f"{GITLAB_FULL_DIFF_CACHE_PREFIX}:gitlab.example.com:trace-analysis:oh-my-grokbuild:1:3:b:s:h")


def test_gitlab_v1_validation_token_contains_version_and_three_refs() -> None:
    token = gitlab_full_diff_v1_validation_token(
        version_id=7,
        base_sha="b",
        start_sha="s",
        head_sha="h",
    )
    assert "7" in token
    assert "b" in token
    assert "s" in token
    assert "h" in token
    assert token == "7:b:s:h"


def test_gitlab_v1_identity_fields() -> None:
    identity = gitlab_full_diff_v1_identity(
        namespace="ns",
        repo="repo",
        iid=1,
        version_id=2,
        base_sha="base",
        start_sha="start",
        head_sha="head",
    )
    assert isinstance(identity, StrictPRDiffCacheIdentity)
    assert identity.schema_version == PRDIFF_CACHE_SCHEMA_V1
    assert identity.cache_key == gitlab_full_diff_v1_key("ns", "repo", 1, 2, "base", "start", "head")
    assert identity.validation_token == "2:base:start:head"


def test_gitlab_v1_identity_immutable() -> None:
    identity = gitlab_full_diff_v1_identity(
        namespace="ns",
        repo="repo",
        iid=1,
        version_id=2,
        base_sha="base",
        start_sha="start",
        head_sha="head",
    )
    with pytest.raises(Exception):
        identity.cache_key = "mutated"  # type: ignore[misc]


def test_unwrap_rejects_legacy_gitlab_key() -> None:
    """Legacy hunk-only keys (gitlab:owner:repo:iid) must not unwrap bare PRDiff."""
    value = _diff()
    assert unwrap_pr_diff_cache_value(value, key="gitlab:owner:repo:1") is None
    # Wrong schema/token path: bare PRDiff under unversioned key is None
    assert unwrap_pr_diff_cache_value(value, key="owner/repo/1") is None
