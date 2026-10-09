"""Cross-request cancellation, capacity, and coalescing cleanup for strict diffs."""

from __future__ import annotations

import anyio
import pytest

from prdiffer.infrastructure.utils.coalescing_service import RequestCoalescingService


@pytest.mark.integration
@pytest.mark.anyio
async def test_coalesced_owner_cancel_allows_same_key_retry() -> None:
    service = RequestCoalescingService(max_waiters=10)
    started = anyio.Event()
    hold = anyio.Event()
    outcomes: dict[str, object] = {}

    async def blocked() -> str:
        started.set()
        await hold.wait()
        return "blocked"

    async def owner() -> None:
        try:
            await service.coalesce("k", blocked, timeout=30.0)
            outcomes["owner"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            outcomes["owner"] = type(exc).__name__

    async with anyio.create_task_group() as tg:
        tg.start_soon(owner)
        await started.wait()
        tg.cancel_scope.cancel()

    stats = await service.get_stats()
    assert stats["pending_count"] == 0
    assert stats["total_waiters"] == 0

    fetch_count = 0

    async def ok() -> str:
        nonlocal fetch_count
        fetch_count += 1
        return "recovered"

    result = await service.coalesce("k", ok, timeout=5.0)
    assert result == "recovered"
    assert fetch_count == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_capacity_one_serializes_two_session_workers() -> None:
    """Two capacity-1 session opens cannot both hold the limiter simultaneously."""
    limiter = anyio.CapacityLimiter(1)
    in_flight = 0
    max_in_flight = 0
    first_hold = anyio.Event()
    second_started = anyio.Event()
    release_first = anyio.Event()

    async def first() -> None:
        nonlocal in_flight, max_in_flight
        async with limiter:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            first_hold.set()
            await release_first.wait()
            in_flight -= 1

    async def second() -> None:
        nonlocal in_flight, max_in_flight
        second_started.set()
        async with limiter:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            in_flight -= 1

    async with anyio.create_task_group() as tg:
        tg.start_soon(first)
        await first_hold.wait()
        tg.start_soon(second)
        await second_started.wait()
        # First still holds capacity; second is blocked on acquire.
        assert max_in_flight == 1
        assert in_flight == 1
        release_first.set()

    assert max_in_flight == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_github_session_capacity_serializes_two_builds() -> None:
    """GitHubPRDiffSession._run_sync holds process limiter across workers."""
    import time
    from unittest.mock import MagicMock

    from prdiffer.domain.entities.pr_diff import PRDiff
    from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
    from prdiffer.infrastructure.github.pr_diff_session import GitHubPRDiffSession

    base_tip = "a" * 40
    merge_base = "b" * 40
    head = "c" * 40
    limiter = anyio.CapacityLimiter(1)
    service = MagicMock()
    in_flight = 0
    max_in_flight = 0
    first_hold = anyio.Event()
    release_first = anyio.Event()
    build_count = 0

    def generate(*args, **kwargs):
        nonlocal build_count
        build_count += 1
        return []

    service._generate_diff_content.side_effect = generate
    service._build_pr_diff_strict.return_value = PRDiff(files=(), head_sha="c" * 40)

    def make_session() -> GitHubPRDiffSession:
        repo = MagicMock()
        pr = MagicMock()
        pr.base.sha = base_tip
        pr.head.sha = head
        pr.changed_files = 0
        merge_commit = MagicMock()
        merge_commit.sha = merge_base
        compare = MagicMock()
        compare.merge_base_commit = merge_commit
        repo.compare.return_value = compare
        repo.get_pull.return_value = pr
        return GitHubPRDiffSession(
            snapshot=PRDiffSnapshot("o", "r", 1, base_tip, merge_base, head, 0),
            github_client=MagicMock(),
            repository=repo,
            pull_request=pr,
            service=service,
            limiter=limiter,
            deadline_monotonic=time.monotonic() + 30,
        )

    original_run = anyio.to_thread.run_sync

    async def tracking_run_sync(func, *args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        if in_flight == 1:
            first_hold.set()
            await release_first.wait()
        try:
            return await original_run(func, *args, **kwargs)
        finally:
            in_flight -= 1

    import prdiffer.infrastructure.github.pr_diff_session as mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod.anyio.to_thread, "run_sync", tracking_run_sync)
    try:
        s1 = make_session()
        s2 = make_session()

        async def first() -> None:
            await s1.build_pr_diff()
            await s1.aclose()

        async def second() -> None:
            await first_hold.wait()
            await s2.build_pr_diff()
            await s2.aclose()

        async with anyio.create_task_group() as tg:
            tg.start_soon(first)
            await first_hold.wait()
            tg.start_soon(second)
            await anyio.sleep(0)
            assert max_in_flight == 1
            release_first.set()

        assert max_in_flight == 1
        assert build_count == 2
    finally:
        monkeypatch.undo()
