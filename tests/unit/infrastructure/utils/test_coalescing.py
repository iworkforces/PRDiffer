"""Tests for RequestCoalescingService request deduplication."""

import pytest
import anyio
from typing import Any
from unittest.mock import Mock, AsyncMock, patch

from prdiffer.infrastructure.utils.coalescing_service import (
    RequestCoalescingService,
    CoalescedRequest,
    CoalescedOwnerCancelledError,
    get_request_coalescing_service,
    DEFAULT_MAX_WAITERS,
)


class ObservedCoalescer(RequestCoalescingService):
    """Signal joined waiters without relying on scheduling delays."""

    def __init__(self) -> None:
        super().__init__(logger=Mock(), max_waiters=50)
        self.joined = anyio.Event()
        self.waiter_gate: anyio.Event | None = None
        self.observed: CoalescedRequest | None = None
        self.cleanup_started = anyio.Event()

    async def _wait_for_request(self, existing_request: CoalescedRequest, key: str, timeout: float) -> Any:
        self.observed = existing_request
        self.joined.set()
        if self.waiter_gate is not None:
            await self.waiter_gate.wait()
        return await super()._wait_for_request(existing_request, key, timeout)

    async def _decrement_waiter(self, request: CoalescedRequest) -> None:
        self.cleanup_started.set()
        await super()._decrement_waiter(request)


@pytest.mark.anyio
async def test_owner_only_cancellation_is_ordinary_for_independent_waiter() -> None:
    service = ObservedCoalescer()
    started, owner_done, waiter_done = anyio.Event(), anyio.Event(), anyio.Event()
    owner_scope = anyio.CancelScope()
    outcomes: list[BaseException] = []
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await anyio.Event().wait()
        return "unreachable"

    async def owner() -> None:
        with owner_scope:
            try:
                await service.coalesce("shared", fetch)
            except anyio.get_cancelled_exc_class() as exc:
                outcomes.append(exc)
                raise
            finally:
                owner_done.set()

    async def waiter() -> None:
        try:
            await service.coalesce("shared", fetch)
        except BaseException as exc:
            outcomes.append(exc)
        finally:
            waiter_done.set()

    with anyio.fail_after(2):
        async with anyio.create_task_group() as group:
            group.start_soon(owner)
            await started.wait()
            group.start_soon(waiter)
            await service.joined.wait()
            assert (await service.get_stats())["total_waiters"] == 2
            owner_scope.cancel()
            await owner_done.wait()
            await waiter_done.wait()
    assert isinstance(outcomes[0], anyio.get_cancelled_exc_class())
    assert isinstance(outcomes[1], Exception)
    assert isinstance(outcomes[1], CoalescedOwnerCancelledError)
    assert outcomes[1].key == "shared"
    assert calls == 1
    assert await service.get_stats() == {"pending_count": 0, "pending_keys": [], "total_waiters": 0}
    retry = AsyncMock(return_value="fresh")
    assert await service.coalesce("shared", retry) == "fresh"
    retry.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [30.0, 0.0])
async def test_waiter_cleanup_is_shielded_while_owner_and_sibling_live(timeout: float) -> None:
    service = ObservedCoalescer()
    started, release, done = anyio.Event(), anyio.Event(), anyio.Event()
    scope = anyio.CancelScope()
    results: list[str] = []
    failures: list[BaseException] = []
    fetch = AsyncMock()

    async def blocked_fetch() -> str:
        await fetch()
        started.set()
        await release.wait()
        return "shared-result"

    async def normal() -> None:
        results.append(await service.coalesce("key", blocked_fetch))

    async def cancelled_waiter() -> None:
        with scope:
            try:
                await service.coalesce("key", blocked_fetch, timeout=timeout)
            except BaseException as exc:
                failures.append(exc)
                if isinstance(exc, anyio.get_cancelled_exc_class()):
                    raise
            finally:
                done.set()

    with anyio.fail_after(2):
        async with anyio.create_task_group() as group:
            group.start_soon(normal)
            await started.wait()
            group.start_soon(normal)
            await service.joined.wait()
            service.joined = anyio.Event()
            group.start_soon(cancelled_waiter)
            await service.joined.wait()
            if timeout:
                async with service._lock:
                    scope.cancel()
                    await service.cleanup_started.wait()
                await done.wait()
            else:
                await done.wait()
            assert (await service.get_stats())["total_waiters"] == 2
            release.set()
    assert len(failures) == 1
    assert isinstance(failures[0], anyio.get_cancelled_exc_class() if timeout else TimeoutError)
    assert results == ["shared-result", "shared-result"]
    fetch.assert_awaited_once()
    assert (await service.get_stats())["pending_count"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("late_failure", [False, True])
async def test_eviction_settles_once_and_old_waiter_cannot_decrement_successor(late_failure: bool) -> None:
    service = ObservedCoalescer()
    service._max_pending_requests = 1
    service.waiter_gate = anyio.Event()
    started, release, old_done, waiter_done = anyio.Event(), anyio.Event(), anyio.Event(), anyio.Event()
    successor_started, successor_release = anyio.Event(), anyio.Event()
    failures: list[Exception] = []
    owner_outcomes: list[str | ValueError] = []
    late_error = ValueError("late failure")

    async def old_fetch() -> str:
        started.set()
        await release.wait()
        if late_failure:
            raise late_error
        return "late success"

    async def old_owner() -> None:
        try:
            owner_outcomes.append(await service.coalesce("key", old_fetch))
        except ValueError as exc:
            owner_outcomes.append(exc)
        finally:
            old_done.set()

    async def old_waiter() -> None:
        try:
            await service.coalesce("key", old_fetch)
        except Exception as exc:
            failures.append(exc)
        finally:
            waiter_done.set()

    async def successor_fetch() -> str:
        successor_started.set()
        await successor_release.wait()
        return "fresh"

    with anyio.fail_after(2):
        async with anyio.create_task_group() as group:
            group.start_soon(old_owner)
            await started.wait()
            group.start_soon(old_waiter)
            await service.joined.wait()
            old_request = service.observed
            assert old_request is not None
            await service.coalesce("evictor", AsyncMock(return_value="eviction"))
            eviction = old_request.exception
            assert isinstance(eviction, TimeoutError)
            group.start_soon(service.coalesce, "key", successor_fetch)
            await successor_started.wait()
            release.set()
            await old_done.wait()
            assert owner_outcomes == [late_error if late_failure else "late success"]
            assert old_request.exception is eviction
            service.waiter_gate.set()
            await waiter_done.wait()
            assert await service.get_stats() == {"pending_count": 1, "pending_keys": ["key"], "total_waiters": 1}
            successor_release.set()
    assert failures == [eviction]
    assert await service.get_stats() == {"pending_count": 0, "pending_keys": [], "total_waiters": 0}


@pytest.mark.anyio
async def test_pending_cap_eviction_wakes_waiter_before_old_owner_finishes() -> None:
    service = ObservedCoalescer()
    service._max_pending_requests = 1
    started, release, waiter_done = anyio.Event(), anyio.Event(), anyio.Event()
    failures: list[Exception] = []
    owner_results: list[str] = []
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "owner-result"

    async def owner() -> None:
        owner_results.append(await service.coalesce("evicted", fetch))

    async def waiter() -> None:
        try:
            await service.coalesce("evicted", fetch)
        except Exception as exc:
            failures.append(exc)
        finally:
            waiter_done.set()

    with anyio.fail_after(2):
        async with anyio.create_task_group() as group:
            group.start_soon(owner)
            await started.wait()
            group.start_soon(waiter)
            await service.joined.wait()
            assert await service.coalesce("new", AsyncMock(return_value="new-result")) == "new-result"
            await waiter_done.wait()
            assert len(failures) == 1 and isinstance(failures[0], TimeoutError)
            assert owner_results == []
            assert calls == 1
            release.set()
    assert owner_results == ["owner-result"]
    assert await service.get_stats() == {"pending_count": 0, "pending_keys": [], "total_waiters": 0}


@pytest.fixture
def coalescing_service():
    """Create RequestCoalescingService with mocked dependencies."""
    mock_logger = Mock()
    with patch("prdiffer.infrastructure.utils.coalescing_service.get_settings_service") as mock_settings:
        mock_settings.return_value.get.return_value = 100
        service = RequestCoalescingService(logger=mock_logger, max_waiters=50)
    return service


@pytest.mark.unit
class TestCoalescedRequest:
    """Tests for CoalescedRequest dataclass."""

    def test_default_values(self):
        """Default values are set correctly."""
        req = CoalescedRequest(key="test_key")
        assert req.key == "test_key"
        assert req.result is None
        assert req.exception is None
        assert req.request_count == 1
        assert isinstance(req.event, anyio.Event)

    def test_custom_values(self):
        """Custom values are stored."""
        req = CoalescedRequest(key="k", request_count=5)
        assert req.request_count == 5


@pytest.mark.unit
class TestRequestCoalescingServiceInit:
    """Tests for service initialization."""

    def test_init_with_logger(self):
        """Logger is stored."""
        mock_logger = Mock()
        with patch("prdiffer.infrastructure.utils.coalescing_service.get_settings_service") as mock_settings:
            mock_settings.return_value.get.return_value = DEFAULT_MAX_WAITERS
            service = RequestCoalescingService(logger=mock_logger, max_waiters=50)
        assert service._logger is mock_logger

    def test_init_max_waiters_from_param(self):
        """max_waiters from parameter is used."""
        with patch("prdiffer.infrastructure.utils.coalescing_service.get_settings_service") as mock_settings:
            mock_settings.return_value.get.return_value = DEFAULT_MAX_WAITERS
            service = RequestCoalescingService(logger=Mock(), max_waiters=25)
        assert service._max_waiters == 25


@pytest.mark.unit
class TestCoalesceBasic:
    """Tests for basic coalesce functionality."""

    @pytest.mark.anyio
    async def test_single_request_executes(self, coalescing_service):
        """Single request executes fetch_func and returns result."""
        fetch_func = AsyncMock(return_value="result_data")

        result = await coalescing_service.coalesce("key1", fetch_func)

        assert result == "result_data"
        fetch_func.assert_called_once()

    @pytest.mark.anyio
    async def test_different_keys_execute_separately(self, coalescing_service):
        """Different keys execute separate fetch functions."""
        fetch1 = AsyncMock(return_value="data1")
        fetch2 = AsyncMock(return_value="data2")

        result1 = await coalescing_service.coalesce("key1", fetch1)
        result2 = await coalescing_service.coalesce("key2", fetch2)

        assert result1 == "data1"
        assert result2 == "data2"
        fetch1.assert_called_once()
        fetch2.assert_called_once()

    @pytest.mark.anyio
    async def test_fetch_exception_propagates(self, coalescing_service):
        """Exception from fetch_func propagates to caller."""
        fetch_func = AsyncMock(side_effect=ValueError("fetch failed"))

        with pytest.raises(ValueError, match="fetch failed"):
            await coalescing_service.coalesce("key1", fetch_func)

    @pytest.mark.anyio
    async def test_timeout_raises(self, coalescing_service):
        """Timeout raises TimeoutError."""

        async def slow_func():
            await anyio.sleep(5)
            return "late"

        with pytest.raises(TimeoutError):
            await coalescing_service.coalesce("key1", slow_func, timeout=0.1)


@pytest.mark.unit
class TestCoalesceDeduplication:
    """Tests for request deduplication behavior."""

    @pytest.mark.anyio
    async def test_concurrent_requests_deduplicated(self, coalescing_service):
        """Concurrent requests for same key share one fetch."""
        call_count = 0

        async def counting_fetch():
            nonlocal call_count
            call_count += 1
            await anyio.sleep(0.1)
            return "shared_result"

        results = []

        async def make_request():
            result = await coalescing_service.coalesce("same_key", counting_fetch)
            results.append(result)

        async with anyio.create_task_group() as tg:
            for _ in range(5):
                tg.start_soon(make_request)

        # All should get the same result
        assert all(r == "shared_result" for r in results)
        # Fetch should have been called only once (or at most twice due to race)
        assert call_count <= 2

    @pytest.mark.anyio
    async def test_sequential_requests_execute_separately(self, coalescing_service):
        """Sequential requests for same key execute separately."""
        call_count = 0

        async def counting_fetch():
            nonlocal call_count
            call_count += 1
            return f"result_{call_count}"

        result1 = await coalescing_service.coalesce("key1", counting_fetch)
        result2 = await coalescing_service.coalesce("key1", counting_fetch)

        assert result1 == "result_1"
        assert result2 == "result_2"
        assert call_count == 2


@pytest.mark.unit
class TestGetStats:
    """Tests for get_stats method."""

    @pytest.mark.anyio
    async def test_stats_empty(self, coalescing_service):
        """Empty service reports zero stats."""
        stats = await coalescing_service.get_stats()
        assert stats["pending_count"] == 0
        assert stats["pending_keys"] == []
        assert stats["total_waiters"] == 0

    @pytest.mark.anyio
    async def test_stats_after_completed_request(self, coalescing_service):
        """Stats are clean after completed request."""
        await coalescing_service.coalesce("key1", AsyncMock(return_value="data"))
        stats = await coalescing_service.get_stats()
        assert stats["pending_count"] == 0


@pytest.mark.unit
class TestClear:
    """Tests for clear method."""

    @pytest.mark.anyio
    async def test_clear_removes_pending(self, coalescing_service):
        """Clear removes all pending requests."""
        await coalescing_service.clear()
        stats = await coalescing_service.get_stats()
        assert stats["pending_count"] == 0


@pytest.mark.unit
class TestGetRequestCoalescingServiceSingleton:
    """Tests for singleton factory function."""

    def test_singleton_returns_instance(self):
        """get_request_coalescing_service returns an instance."""
        with patch(
            "prdiffer.infrastructure.utils.coalescing_service._request_coalescing_service",
            None,
        ):
            service = get_request_coalescing_service()
            assert isinstance(service, RequestCoalescingService)


@pytest.mark.unit
class TestCoalescingCancellationCleanup:
    """Owner cancellation must wake waiters and clear pending state."""

    @pytest.mark.anyio
    async def test_owner_cancel_wakes_waiter_and_clears_pending(self, coalescing_service):
        owner_started = anyio.Event()
        release_owner = anyio.Event()
        outcomes: dict[str, object] = {}

        async def slow_fetch():
            owner_started.set()
            await release_owner.wait()
            return "should-not-return"

        async def owner():
            try:
                await coalescing_service.coalesce("same", slow_fetch, timeout=30.0)
                outcomes["owner"] = "ok"
            except BaseException as exc:  # noqa: BLE001 — assert cancel identity
                outcomes["owner"] = type(exc)

        async def waiter():
            await owner_started.wait()

            async def should_not_run():
                raise AssertionError("waiter must not start a second fetch")

            try:
                await coalescing_service.coalesce("same", should_not_run, timeout=30.0)
                outcomes["waiter"] = "ok"
            except BaseException as exc:  # noqa: BLE001
                outcomes["waiter"] = type(exc)

        async with anyio.create_task_group() as tg:
            tg.start_soon(owner)
            tg.start_soon(waiter)
            await owner_started.wait()
            # Cancel the whole group → owner cancelled mid-fetch; waiter must terminate.
            tg.cancel_scope.cancel()

        stats = await coalescing_service.get_stats()
        assert stats["pending_count"] == 0
        assert stats["total_waiters"] == 0
        assert stats["pending_keys"] == []
        # Both sides terminated (cancel), not timed out hanging.
        assert outcomes.get("owner") is not None
        assert outcomes.get("waiter") is not None

    @pytest.mark.anyio
    async def test_same_key_works_after_cancelled_owner(self, coalescing_service):
        owner_started = anyio.Event()
        hold = anyio.Event()

        async def blocked_fetch():
            owner_started.set()
            await hold.wait()
            return "blocked"

        async with anyio.create_task_group() as tg:
            tg.start_soon(coalescing_service.coalesce, "k", blocked_fetch)
            await owner_started.wait()
            tg.cancel_scope.cancel()

        stats = await coalescing_service.get_stats()
        assert stats["pending_count"] == 0

        fetch = AsyncMock(return_value="recovered")
        result = await coalescing_service.coalesce("k", fetch, timeout=5.0)
        assert result == "recovered"
        fetch.assert_awaited_once()


@pytest.mark.unit
class TestCoalescingImportSurface:
    def test_package_path_removed(self):
        """Legacy utils.coalescing package shim must not exist."""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("prdiffer.infrastructure.utils.coalescing")
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("prdiffer.infrastructure.utils.coalescing.service")


@pytest.mark.unit
class TestMaxWaitersOverflow:
    @pytest.mark.anyio
    async def test_overflow_does_not_replace_pending_owner(self):
        """When max_waiters is hit, overflow runs standalone without clobbering owner."""
        service = RequestCoalescingService(logger=Mock(), max_waiters=1)
        owner_started = anyio.Event()
        release_owner = anyio.Event()
        overflow_ran = anyio.Event()
        owner_fetches = 0
        overflow_fetches = 0

        async def owner_fetch():
            nonlocal owner_fetches
            owner_fetches += 1
            owner_started.set()
            await release_owner.wait()
            return "owner"

        async def overflow_fetch():
            nonlocal overflow_fetches
            overflow_fetches += 1
            overflow_ran.set()
            return "overflow"

        async with anyio.create_task_group() as tg:

            async def run_owner():
                result = await service.coalesce("k", owner_fetch, timeout=5.0)
                assert result == "owner"

            tg.start_soon(run_owner)
            await owner_started.wait()
            # max_waiters=1 and owner already counts as 1 → overflow standalone
            overflow_result = await service.coalesce("k", overflow_fetch, timeout=5.0)
            assert overflow_result == "overflow"
            await overflow_ran.wait()
            stats = await service.get_stats()
            # Original owner still pending (not replaced)
            assert stats["pending_count"] == 1
            assert "k" in stats["pending_keys"]
            release_owner.set()

        assert owner_fetches == 1
        assert overflow_fetches == 1
