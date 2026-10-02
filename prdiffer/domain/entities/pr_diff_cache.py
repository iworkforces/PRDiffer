"""Provider-neutral strict session cache identity for PRDiff values."""

from __future__ import annotations

from dataclasses import dataclass

from prdiffer.domain.entities.pr_diff import PRDiff

PRDIFF_CACHE_SCHEMA_V1 = 1
PRDIFF_CACHE_SCHEMA_V2 = 2
# GitHub strict identity prefix (merge-base + head).
GITHUB_FULL_DIFF_CACHE_PREFIX = "github-full-diff-v3"
GITLAB_FULL_DIFF_CACHE_PREFIX = "gitlab-full-diff-v1"
_STRICT_KEY_PREFIXES = (GITHUB_FULL_DIFF_CACHE_PREFIX, GITLAB_FULL_DIFF_CACHE_PREFIX)


@dataclass(frozen=True)
class StrictPRDiffCacheIdentity:
    """Provider-neutral cache key + validation token for a strict session."""

    cache_key: str
    validation_token: str
    schema_version: int


def github_full_diff_v3_key(
    owner: str,
    repo: str,
    pr_number: int,
    merge_base_sha: str,
    head_sha: str,
) -> str:
    """Exact GitHub PRDiff cache key for the session/v3 merge-base path."""
    return f"{GITHUB_FULL_DIFF_CACHE_PREFIX}:{owner.casefold()}:{repo.casefold()}:{pr_number}:{merge_base_sha}:{head_sha}"


def github_full_diff_v3_validation_token(merge_base_sha: str, head_sha: str) -> str:
    """Validation token binds the same immutable merge-base and head refs as the key."""
    return f"{merge_base_sha}:{head_sha}"


def github_full_diff_v3_identity(
    owner: str,
    repo: str,
    pr_number: int,
    merge_base_sha: str,
    head_sha: str,
) -> StrictPRDiffCacheIdentity:
    """Strict session identity for GitHub full-diff v3 (merge-base + head)."""
    return StrictPRDiffCacheIdentity(
        cache_key=github_full_diff_v3_key(owner, repo, pr_number, merge_base_sha, head_sha),
        validation_token=github_full_diff_v3_validation_token(merge_base_sha, head_sha),
        schema_version=PRDIFF_CACHE_SCHEMA_V2,
    )


def gitlab_full_diff_v1_key(
    namespace: str,
    repo: str,
    iid: int,
    version_id: int | str,
    base_sha: str,
    start_sha: str,
    head_sha: str,
    *,
    host: str = "gitlab.com",
) -> str:
    """Exact GitLab strict full-diff v1 cache key (includes host for multi-instance).

    Format: ``gitlab-full-diff-v1:{host}:{ns}:{repo}:{iid}:{ver}:{base}:{start}:{head}``
    """
    return f"{GITLAB_FULL_DIFF_CACHE_PREFIX}:{host.casefold()}:{namespace.casefold()}:{repo.casefold()}:{iid}:{version_id}:{base_sha}:{start_sha}:{head_sha}"


def gitlab_full_diff_v1_validation_token(
    version_id: int | str,
    base_sha: str,
    start_sha: str,
    head_sha: str,
) -> str:
    """Validation token: version ID plus base/start/head SHAs."""
    return f"{version_id}:{base_sha}:{start_sha}:{head_sha}"


def gitlab_full_diff_v1_identity(
    namespace: str,
    repo: str,
    iid: int,
    version_id: int | str,
    base_sha: str,
    start_sha: str,
    head_sha: str,
    *,
    host: str = "gitlab.com",
) -> StrictPRDiffCacheIdentity:
    """Strict session identity for GitLab full-diff v1 (host-aware)."""
    return StrictPRDiffCacheIdentity(
        cache_key=gitlab_full_diff_v1_key(namespace, repo, iid, version_id, base_sha, start_sha, head_sha, host=host),
        validation_token=gitlab_full_diff_v1_validation_token(version_id, base_sha, start_sha, head_sha),
        schema_version=PRDIFF_CACHE_SCHEMA_V1,
    )


def unwrap_pr_diff_cache_value(
    raw: object,
    *,
    key: str = "",
    identity: StrictPRDiffCacheIdentity | None = None,
) -> PRDiff | None:
    """Accept a bare PRDiff cached under a strict GitHub-v3 or GitLab-v1 key.

    When ``identity`` is given the key must equal ``identity.cache_key`` exactly.
    Unknown or non-strict keys miss.
    """
    if not isinstance(raw, PRDiff):
        return None
    if identity is not None and key != identity.cache_key:
        return None
    return raw if key.startswith(_STRICT_KEY_PREFIXES) else None
