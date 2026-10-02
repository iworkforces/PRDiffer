"""Tests for cache repository models: CacheEntry dataclass and with_lock decorator."""

from threading import RLock
from unittest.mock import MagicMock

import pytest

from prdiffer.infrastructure.cache.cache_repository import CacheEntry, with_lock


def _make_repo_mock() -> MagicMock:
    """Create a minimal mock repository."""
    mock = MagicMock()
    mock.repo_owner = "owner"
    mock.repo_name = "repo"
    mock.pr_number = 1
    return mock


@pytest.mark.unit
class TestCacheEntry:
    """Test CacheEntry dataclass."""

    def test_create_cache_entry(self):
        repo = _make_repo_mock()
        entry = CacheEntry(repository=repo, timestamp=100.0, initialized=True)
        assert entry.repository is repo
        assert entry.timestamp == 100.0
        assert entry.initialized is True

    def test_cache_entry_mutable_timestamp(self):
        """CacheEntry is a regular (non-frozen) dataclass — timestamp can be updated."""
        repo = _make_repo_mock()
        entry = CacheEntry(repository=repo, timestamp=100.0, initialized=True)
        entry.timestamp = 200.0
        assert entry.timestamp == 200.0

    def test_cache_entry_initialized_false(self):
        repo = _make_repo_mock()
        entry = CacheEntry(repository=repo, timestamp=0.0, initialized=False)
        assert entry.initialized is False


@pytest.mark.unit
class TestWithLockDecorator:
    """Test with_lock decorator for thread-safe method access."""

    def test_decorated_method_acquires_lock(self):
        """Verify the decorator acquires self._lock before calling the method."""

        class MyService:
            def __init__(self):
                self._lock = RLock()
                self.call_log: list[str] = []

            @with_lock()
            def do_work(self, value: str) -> str:
                self.call_log.append(value)
                return value

        svc = MyService()
        result = svc.do_work("hello")
        assert result == "hello"
        assert svc.call_log == ["hello"]

    def test_decorated_method_custom_lock_attr(self):
        """Verify with_lock respects custom lock_attr argument."""

        class MyService:
            def __init__(self):
                self._my_lock = RLock()

            @with_lock(lock_attr="_my_lock")
            def do_work(self) -> str:
                return "ok"

        svc = MyService()
        assert svc.do_work() == "ok"

    def test_decorated_method_raises_if_lock_missing(self):
        """If the lock attribute does not exist, should raise AttributeError."""

        class MyService:
            @with_lock(lock_attr="_nonexistent_lock")
            def do_work(self) -> str:
                return "ok"

        svc = MyService()
        with pytest.raises(AttributeError):
            svc.do_work()

    def test_decorated_method_preserves_return_value(self):
        class MyService:
            def __init__(self):
                self._lock = RLock()

            @with_lock()
            def compute(self, a: int, b: int) -> int:
                return a + b

        svc = MyService()
        assert svc.compute(3, 7) == 10

    def test_decorated_method_preserves_exception(self):
        class MyService:
            def __init__(self):
                self._lock = RLock()

            @with_lock()
            def fail(self) -> None:
                raise ValueError("boom")

        svc = MyService()
        with pytest.raises(ValueError, match="boom"):
            svc.fail()

    def test_lock_is_reentrant(self):
        """RLock allows same thread to acquire multiple times."""

        class MyService:
            def __init__(self):
                self._lock = RLock()

            @with_lock()
            def outer(self) -> str:
                return self.inner()

            @with_lock()
            def inner(self) -> str:
                return "nested"

        svc = MyService()
        assert svc.outer() == "nested"
