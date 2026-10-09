"""Tests for GitHub PR diff session isolation and v3 snapshot identity."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import anyio
import pytest
from github import GithubException

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import (
    PRDIFF_CACHE_SCHEMA_V2,
    github_full_diff_v3_identity,
    github_full_diff_v3_key,
)
from prdiffer.domain.error_codes import E5004_TIMEOUT_ERROR
from prdiffer.domain.exceptions import (
    FullDiffIncompleteError,
    FullDiffIncompleteReason,
    GitHubAPIError,
    TimeoutError as DomainTimeoutError,
)
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
from prdiffer.infrastructure.github.pr_diff_session import (
    GitHubPRDiffSession,
    _capture_github_snapshot,
    revalidate_github_snapshot,
)

BASE_TIP = "a" * 40
MERGE_BASE = "b" * 40
HEAD = "c" * 40
HEAD2 = "d" * 40
MERGE_BASE2 = "e" * 40
BASE_TIP2 = "f" * 40


def _snapshot(
    *,
    base_tip: str = BASE_TIP,
    merge_base: str = MERGE_BASE,
    head: str = HEAD,
    count: int = 3,
) -> PRDiffSnapshot:
    return PRDiffSnapshot("Owner", "Repo", 7, base_tip, merge_base, head, count)


@pytest.mark.unit
def test_session_cache_identity_matches_github_v3_bytes() -> None:
    snapshot = _snapshot()
    session = GitHubPRDiffSession(
        snapshot=snapshot,
        github_client=MagicMock(),
        repository=MagicMock(),
        pull_request=MagicMock(),
        service=MagicMock(),
        limiter=anyio.CapacityLimiter(1),
        deadline_monotonic=time.monotonic() + 30,
    )
    identity = session.cache_identity
    assert identity.cache_key == github_full_diff_v3_key("Owner", "Repo", 7, MERGE_BASE, HEAD)
    assert identity.cache_key == f"github-full-diff-v3:owner:repo:7:{MERGE_BASE}:{HEAD}"
    assert identity.validation_token == f"{MERGE_BASE}:{HEAD}"
    assert identity.schema_version == PRDIFF_CACHE_SCHEMA_V2


@pytest.mark.unit
def test_v3_identity_stable_when_only_base_tip_changes() -> None:
    a = github_full_diff_v3_identity("o", "r", 1, MERGE_BASE, HEAD)
    b = github_full_diff_v3_identity("o", "r", 1, MERGE_BASE, HEAD)
    # base tip is not part of the key — identity ignores base-tip-only churn
    assert a.cache_key == b.cache_key
    assert a.validation_token == b.validation_token
    different_mb = github_full_diff_v3_identity("o", "r", 1, MERGE_BASE2, HEAD)
    assert different_mb.cache_key != a.cache_key
    assert different_mb.validation_token != a.validation_token


@pytest.mark.unit
def test_capture_snapshot_resolves_merge_base_without_base_tip_fallback() -> None:
    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = 2
    merge_commit = MagicMock()
    merge_commit.sha = MERGE_BASE
    compare = MagicMock()
    compare.merge_base_commit = merge_commit
    repo.compare.return_value = compare

    snap = _capture_github_snapshot(
        repo_owner="o",
        repo_name="r",
        pr_number=9,
        repository=repo,
        pull_request=pr,
    )
    assert snap.base_tip_sha == BASE_TIP
    assert snap.merge_base_sha == MERGE_BASE
    assert snap.head_sha == HEAD
    assert snap.authoritative_changed_files == 2
    repo.compare.assert_called_once_with(BASE_TIP, HEAD)


@pytest.mark.unit
def test_capture_snapshot_rejects_boolean_changed_files() -> None:
    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = True
    with pytest.raises(FullDiffIncompleteError) as ei:
        _capture_github_snapshot(
            repo_owner="o",
            repo_name="r",
            pr_number=1,
            repository=repo,
            pull_request=pr,
        )
    assert ei.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED
    repo.compare.assert_not_called()


@pytest.mark.unit
def test_capture_snapshot_compare_failure_is_operational_not_empty() -> None:
    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = 1
    repo.compare.side_effect = GithubException(500, {"message": "boom"}, None)
    with pytest.raises(GitHubAPIError) as ei:
        _capture_github_snapshot(
            repo_owner="o",
            repo_name="r",
            pr_number=1,
            repository=repo,
            pull_request=pr,
        )
    assert ei.value.error_code.code == "E5002"
    assert not isinstance(ei.value, FullDiffIncompleteError)


@pytest.mark.unit
def test_capture_snapshot_missing_merge_base_is_incomplete() -> None:
    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = 1
    compare = MagicMock()
    compare.merge_base_commit = None
    repo.compare.return_value = compare
    with pytest.raises(FullDiffIncompleteError) as ei:
        _capture_github_snapshot(
            repo_owner="o",
            repo_name="r",
            pr_number=1,
            repository=repo,
            pull_request=pr,
        )
    assert ei.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED


@pytest.mark.unit
def test_revalidate_accepts_base_tip_only_change() -> None:
    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP2  # tip moved
    pr.head.sha = HEAD
    pr.changed_files = 3
    merge_commit = MagicMock()
    merge_commit.sha = MERGE_BASE
    compare = MagicMock()
    compare.merge_base_commit = merge_commit
    repo.compare.return_value = compare
    repo.get_pull.return_value = pr
    revalidate_github_snapshot(repo, _snapshot(count=3))


@pytest.mark.unit
def test_revalidate_raises_snapshot_changed_on_head_drift() -> None:
    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD2
    pr.changed_files = 3
    merge_commit = MagicMock()
    merge_commit.sha = MERGE_BASE
    compare = MagicMock()
    compare.merge_base_commit = merge_commit
    repo.compare.return_value = compare
    repo.get_pull.return_value = pr
    with pytest.raises(FullDiffIncompleteError) as ei:
        revalidate_github_snapshot(repo, _snapshot(count=3))
    assert ei.value.reason is FullDiffIncompleteReason.SNAPSHOT_CHANGED


@pytest.mark.unit
def test_revalidate_raises_snapshot_changed_on_count_drift() -> None:
    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = 99
    merge_commit = MagicMock()
    merge_commit.sha = MERGE_BASE
    compare = MagicMock()
    compare.merge_base_commit = merge_commit
    repo.compare.return_value = compare
    repo.get_pull.return_value = pr
    with pytest.raises(FullDiffIncompleteError) as ei:
        revalidate_github_snapshot(repo, _snapshot(count=3))
    assert ei.value.reason is FullDiffIncompleteReason.SNAPSHOT_CHANGED


@pytest.mark.unit
def test_revalidate_raises_snapshot_changed_on_merge_base_drift() -> None:
    """Merge-base-only drift (head/count stable) must raise SNAPSHOT_CHANGED."""
    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = 3
    merge_commit = MagicMock()
    merge_commit.sha = MERGE_BASE2
    compare = MagicMock()
    compare.merge_base_commit = merge_commit
    repo.compare.return_value = compare
    repo.get_pull.return_value = pr
    with pytest.raises(FullDiffIncompleteError) as ei:
        revalidate_github_snapshot(repo, _snapshot(count=3))
    assert ei.value.reason is FullDiffIncompleteReason.SNAPSHOT_CHANGED


@pytest.mark.unit
@pytest.mark.anyio
async def test_session_build_passes_snapshot_and_revalidates() -> None:
    limiter = anyio.CapacityLimiter(1)
    service = MagicMock()
    service._generate_diff_content.return_value = []
    service._build_pr_diff_strict.return_value = PRDiff(files=(), head_sha=HEAD)

    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = 0
    merge_commit = MagicMock()
    merge_commit.sha = MERGE_BASE
    compare = MagicMock()
    compare.merge_base_commit = merge_commit
    repo.compare.return_value = compare
    repo.get_pull.return_value = pr

    session = GitHubPRDiffSession(
        snapshot=_snapshot(count=0),
        github_client=MagicMock(),
        repository=repo,
        pull_request=pr,
        service=service,
        limiter=limiter,
        deadline_monotonic=time.monotonic() + 30,
    )
    result = await session.build_pr_diff()
    assert result == PRDiff(files=(), head_sha=HEAD)
    service._build_pr_diff_strict.assert_called_once_with([], head_sha=HEAD)
    service._generate_diff_content.assert_called_once()
    kwargs = service._generate_diff_content.call_args.kwargs
    assert kwargs["snapshot"].merge_base_sha == MERGE_BASE
    repo.get_pull.assert_called()
    await session.aclose()


@pytest.mark.unit
@pytest.mark.anyio
async def test_session_build_rejects_late_worker_after_deadline() -> None:
    """Post-worker budget check discards results after the deadline elapses during work."""
    limiter = anyio.CapacityLimiter(1)
    service = MagicMock()
    service._generate_diff_content.return_value = []
    service._build_pr_diff_strict.return_value = PRDiff(files=(), head_sha=HEAD)

    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = 0
    merge_commit = MagicMock()
    merge_commit.sha = MERGE_BASE
    compare = MagicMock()
    compare.merge_base_commit = merge_commit
    repo.compare.return_value = compare
    repo.get_pull.return_value = pr

    # Deadline already past: _run_sync pre-check fails before worker.
    session = GitHubPRDiffSession(
        snapshot=_snapshot(count=0),
        github_client=MagicMock(),
        repository=repo,
        pull_request=pr,
        service=service,
        limiter=limiter,
        deadline_monotonic=time.monotonic() - 1.0,
    )
    with pytest.raises(DomainTimeoutError) as ei:
        await session.build_pr_diff()
    assert ei.value.error_code is E5004_TIMEOUT_ERROR
    service._generate_diff_content.assert_not_called()
    await session.aclose()


@pytest.mark.unit
@pytest.mark.anyio
async def test_session_build_rejects_when_capacity_wait_exhausts_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """After capacity is acquired, re-check rejects overdue work (GitLab-parity)."""
    limiter = anyio.CapacityLimiter(1)
    service = MagicMock()
    service._generate_diff_content.return_value = []
    service._build_pr_diff_strict.return_value = PRDiff(files=(), head_sha=HEAD)

    repo = MagicMock()
    pr = MagicMock()
    session = GitHubPRDiffSession(
        snapshot=_snapshot(count=0),
        github_client=MagicMock(),
        repository=repo,
        pull_request=pr,
        service=service,
        limiter=limiter,
        deadline_monotonic=time.monotonic() + 60.0,
    )

    calls = {"n": 0}
    real_monotonic = time.monotonic

    def fake_monotonic() -> float:
        calls["n"] += 1
        # First pre-check OK; post-acquire check exhausts budget.
        if calls["n"] <= 1:
            return real_monotonic()
        return real_monotonic() + 120.0

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    with pytest.raises(DomainTimeoutError) as ei:
        await session.build_pr_diff()
    assert ei.value.error_code is E5004_TIMEOUT_ERROR
    service._generate_diff_content.assert_not_called()
    await session.aclose()


@pytest.mark.unit
@pytest.mark.anyio
async def test_session_build_uses_limiter_capacity_one() -> None:
    limiter = anyio.CapacityLimiter(1)
    service = MagicMock()
    service._generate_diff_content.return_value = []
    service._build_pr_diff_strict.return_value = PRDiff(files=(), head_sha=HEAD)

    in_flight = 0
    max_in_flight = 0

    original_run = anyio.to_thread.run_sync

    async def tracking_run_sync(func, *args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            return await original_run(func, *args, **kwargs)
        finally:
            in_flight -= 1

    repo = MagicMock()
    pr = MagicMock()
    pr.base.sha = BASE_TIP
    pr.head.sha = HEAD
    pr.changed_files = 0
    merge_commit = MagicMock()
    merge_commit.sha = MERGE_BASE
    compare = MagicMock()
    compare.merge_base_commit = merge_commit
    repo.compare.return_value = compare
    repo.get_pull.return_value = pr

    session = GitHubPRDiffSession(
        snapshot=_snapshot(count=0),
        github_client=MagicMock(),
        repository=repo,
        pull_request=pr,
        service=service,
        limiter=limiter,
        deadline_monotonic=time.monotonic() + 30,
    )

    import prdiffer.infrastructure.github.pr_diff_session as mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod.anyio.to_thread, "run_sync", tracking_run_sync)
    try:
        result = await session.build_pr_diff()
        assert isinstance(result, PRDiff)
        assert max_in_flight == 1
    finally:
        monkeypatch.undo()
        await session.aclose()


@pytest.mark.unit
@pytest.mark.anyio
async def test_session_closes_idempotently() -> None:
    session = GitHubPRDiffSession(
        snapshot=_snapshot(count=0),
        github_client=MagicMock(),
        repository=MagicMock(),
        pull_request=MagicMock(),
        service=MagicMock(),
        limiter=anyio.CapacityLimiter(1),
        deadline_monotonic=time.monotonic() + 30,
    )
    await session.aclose()
    await session.aclose()
    assert session._closed is True
