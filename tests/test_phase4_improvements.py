"""Tests for Phase 4: Architecture Refinement improvements."""

import pytest
import anyio


class TestGitHubConfig:
    def test_default_values(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig()

        assert config.rate_limit == 5000
        assert config.timeout == 30
        assert config.max_retries == 3
        assert config.retry_delay == 1.0
        assert config.circuit_breaker_enabled is True
        assert config.diff_parallel_threshold == 3

    def test_from_dict(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        data = {
            "rate_limit": 1000,
            "timeout": 60,
            "max_retries": 5,
            "ignore_patterns": ["*.lock", "node_modules/"],
            "valid_extensions": [".py", ".js"],
        }

        config = GitHubConfig.from_dict(data)

        assert config.rate_limit == 1000
        assert config.timeout == 60
        assert config.max_retries == 5
        assert config.ignore_patterns == ("*.lock", "node_modules/")
        assert config.valid_extensions == (".py", ".js")

    def test_to_dict(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig(
            rate_limit=2000,
            timeout=45,
            ignore_patterns=("*.log",),
            valid_extensions=(".py",),
        )

        data = config.to_dict()

        assert data["rate_limit"] == 2000
        assert data["timeout"] == 45
        assert data["ignore_patterns"] == ["*.log"]
        assert data["valid_extensions"] == [".py"]

    def test_with_overrides(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        original = GitHubConfig(rate_limit=1000)
        overridden = original.with_overrides(rate_limit=2000, timeout=60)

        assert original.rate_limit == 1000  # Original unchanged
        assert overridden.rate_limit == 2000
        assert overridden.timeout == 60

    def test_immutability(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig()

        # frozen dataclass prevents mutation via normal assignment
        with pytest.raises(AttributeError):
            setattr(config, "rate_limit", 9999)

    def test_should_ignore_file(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig(ignore_patterns=("*.lock", "node_modules/", "*.log"))

        assert config.should_ignore_file("package-lock.json") is False  # *.lock != *-lock.json
        assert config.should_ignore_file("yarn.lock") is True
        assert config.should_ignore_file("src/node_modules/lib.js") is True
        assert config.should_ignore_file("debug.log") is True
        assert config.should_ignore_file("main.py") is False

    def test_has_valid_extension(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig(valid_extensions=(".py", ".js", ".ts"))

        assert config.has_valid_extension("main.py") is True
        assert config.has_valid_extension("index.js") is True
        assert config.has_valid_extension("app.ts") is True
        assert config.has_valid_extension("style.css") is False
        assert config.has_valid_extension("README") is False

    def test_has_valid_extension_empty(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig(valid_extensions=())

        assert config.has_valid_extension("anything.xyz") is True

    def test_should_process_file(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig(
            ignore_patterns=("*.lock", "node_modules/"),
            valid_extensions=(".py", ".js"),
        )

        assert config.should_process_file("main.py") is True
        assert config.should_process_file("yarn.lock") is False  # Ignored
        assert config.should_process_file("style.css") is False  # Invalid extension

    def test_helper_properties(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig(
            circuit_breaker_enabled=True,
            adaptive_retry_enabled=False,
            api_health_tracking=True,
        )

        assert config.should_use_circuit_breaker is True
        assert config.should_use_adaptive_retry is False
        assert config.should_track_api_health is True


class TestSettingsServiceGitHubConfig:
    def test_get_github_config_returns_config_object(self):
        from prdiffer.infrastructure.settings import SettingsService
        from prdiffer.domain.config.github_config import GitHubConfig

        service = SettingsService()
        config = service.get_github_config()

        assert isinstance(config, GitHubConfig)
        assert config.rate_limit > 0
        assert config.timeout > 0


class TestAsyncParallelExecutor:
    @pytest.mark.asyncio
    async def test_execute_batch_basic(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )

        async def double(x: int) -> int:
            return x * 2

        executor = AsyncParallelExecutor(max_concurrent=5)
        results = await executor.execute_batch(double, [1, 2, 3, 4, 5])

        assert sorted(results) == [2, 4, 6, 8, 10]

    @pytest.mark.asyncio
    async def test_execute_batch_empty(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )

        async def double(x: int) -> int:
            return x * 2

        executor = AsyncParallelExecutor()
        results = await executor.execute_batch(double, [])

        assert results == []

    @pytest.mark.asyncio
    async def test_execute_batch_with_errors_ignore(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )
        from prdiffer.infrastructure.utils.parallel.results import (
            ErrorStrategy,
        )

        async def maybe_fail(x: int) -> int:
            if x == 3:
                raise ValueError("Error on 3")
            return x * 2

        executor = AsyncParallelExecutor(error_strategy=ErrorStrategy.IGNORE)
        results = await executor.execute_batch(maybe_fail, [1, 2, 3, 4, 5])

        assert sorted(results) == [2, 4, 8, 10]  # 3 is skipped due to error

    @pytest.mark.asyncio
    async def test_execute_batch_with_errors_raise(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )
        from prdiffer.infrastructure.utils.parallel.results import (
            ErrorStrategy,
        )

        async def always_fail(x: int) -> int:
            raise ValueError(f"Error on {x}")

        executor = AsyncParallelExecutor(error_strategy=ErrorStrategy.RAISE)

        with pytest.raises(Exception):  # ExceptionGroup in anyio
            await executor.execute_batch(always_fail, [1, 2, 3])

    @pytest.mark.asyncio
    async def test_execute_batch_with_context(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )

        async def multiply_by_factor(x: int, context: dict[str, int]) -> int:
            return x * context["factor"]

        executor = AsyncParallelExecutor()
        results = await executor.execute_batch_with_context(multiply_by_factor, [1, 2, 3], {"factor": 10})

        assert sorted(results) == [10, 20, 30]

    @pytest.mark.asyncio
    async def test_execute_batch_with_progress(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )

        progress_calls = []

        def track_progress(completed: int, total: int):
            progress_calls.append((completed, total))

        async def double(x: int) -> int:
            return x * 2

        executor = AsyncParallelExecutor(max_concurrent=1)  # Sequential for predictable progress
        results = await executor.execute_with_progress(double, [1, 2, 3], progress_callback=track_progress)

        assert sorted(results) == [2, 4, 6]
        assert len(progress_calls) == 3
        assert progress_calls[-1] == (3, 3)

    @pytest.mark.asyncio
    async def test_execute_batch_detailed(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )

        async def maybe_fail(x: int) -> int:
            if x == 2:
                raise ValueError("Error on 2")
            return x * 2

        executor = AsyncParallelExecutor()
        result = await executor.execute_batch_detailed(maybe_fail, [1, 2, 3])

        assert result.success_count == 2
        assert result.failure_count == 1
        assert sorted(result.successful) == [2, 6]
        assert len(result.failed) == 1

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )

        max_concurrent_observed = 0
        current_concurrent = 0
        lock = anyio.Lock()

        async def track_concurrency(x: int) -> int:
            nonlocal max_concurrent_observed, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent_observed:
                    max_concurrent_observed = current_concurrent
            await anyio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return x

        executor = AsyncParallelExecutor(max_concurrent=3)
        await executor.execute_batch(track_concurrency, list(range(10)))

        assert max_concurrent_observed <= 3

    def test_get_stats(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )
        from prdiffer.infrastructure.utils.parallel.results import (
            ErrorStrategy,
        )

        executor = AsyncParallelExecutor(
            max_concurrent=10,
            timeout=30.0,
            error_strategy=ErrorStrategy.COLLECT,
        )

        stats = executor.get_stats()

        assert stats["max_concurrent"] == 10
        assert stats["timeout"] == 30.0
        assert stats["error_strategy"] == "collect"


class TestBatchResult:
    def test_batch_result_properties(self):
        from prdiffer.infrastructure.utils.parallel.results import BatchResult

        result = BatchResult[int](
            successful=[1, 2, 3],
            failed=[(4, ValueError("error")), (5, RuntimeError("error"))],
        )

        assert result.total == 5
        assert result.success_count == 3
        assert result.failure_count == 2
        assert result.success_rate == 60.0
        assert result.all_succeeded is False

    def test_batch_result_all_success(self):
        from prdiffer.infrastructure.utils.parallel.results import BatchResult

        result = BatchResult[str](successful=["a", "b", "c"], failed=[])

        assert result.all_succeeded is True
        assert result.success_rate == 100.0

    def test_batch_result_get_errors(self):
        from prdiffer.infrastructure.utils.parallel.results import BatchResult

        e1 = ValueError("error1")
        e2 = RuntimeError("error2")
        result = BatchResult[int](
            successful=[1],
            failed=[(2, e1), (3, e2)],
        )

        errors = result.get_errors()
        assert len(errors) == 2
        assert e1 in errors
        assert e2 in errors


def _reset_circuit_breaker_registry():
    import prdiffer.infrastructure.utils.circuit_breaker_registry as cb_module

    cb_module.GlobalCircuitBreakerRegistry._instance = None
    cb_module.GlobalCircuitBreakerRegistry._initialized = False
    cb_module._global_circuit_breaker_registry = None


class TestGlobalCircuitBreakerRegistry:
    def test_singleton_pattern(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            GlobalCircuitBreakerRegistry,
        )

        _reset_circuit_breaker_registry()

        registry1 = GlobalCircuitBreakerRegistry()
        registry2 = GlobalCircuitBreakerRegistry()

        assert registry1 is registry2

    def test_get_breaker_creates_new(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()
        breaker = registry.get_breaker("test_endpoint")

        assert breaker is not None
        assert "test_endpoint" in registry.get_all_stats()

    def test_get_breaker_returns_same(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()
        breaker1 = registry.get_breaker("endpoint_a")
        breaker2 = registry.get_breaker("endpoint_a")

        assert breaker1 is breaker2

    def test_can_execute_checks_both_breakers(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()

        assert registry.can_execute("test_endpoint") is True

    def test_record_success_updates_both(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()
        registry.record_success("my_endpoint")

        stats = registry.get_all_stats()
        assert "my_endpoint" in stats
        assert "global" in stats

    def test_record_failure_updates_both(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()

        for _ in range(3):
            registry.record_failure("failing_endpoint")

        stats = registry.get_all_stats()
        assert stats["failing_endpoint"]["failure_count"] == 3
        assert stats["global"]["failure_count"] == 3

    def test_get_all_stats(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()
        registry.get_breaker("endpoint_1")
        registry.get_breaker("endpoint_2")

        stats = registry.get_all_stats()

        assert "global" in stats
        assert "endpoint_1" in stats
        assert "endpoint_2" in stats

    def test_get_open_breakers(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()

        assert registry.get_open_breakers() == []

        breaker = registry.get_breaker("failing_endpoint")
        # Low threshold to trigger opening in test
        breaker.failure_threshold = 2

        for _ in range(3):
            registry.record_failure("failing_endpoint")

        open_breakers = registry.get_open_breakers()
        assert "failing_endpoint" in open_breakers

    def test_reset_all(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )
        from prdiffer.infrastructure.utils.circuit_breaker_core import (
            CircuitState,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()
        breaker = registry.get_breaker("endpoint_to_reset")
        breaker.failure_threshold = 1  # Low threshold for test

        registry.record_failure("endpoint_to_reset")
        registry.record_failure("endpoint_to_reset")

        registry.reset_all()

        stats = registry.get_all_stats()
        assert stats["global"]["state"] == CircuitState.CLOSED.value

    def test_clear_endpoint(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()
        registry.get_breaker("to_remove")

        assert "to_remove" in registry.get_all_stats()

        registry.clear_endpoint("to_remove")

        assert "to_remove" not in registry.get_all_stats()


class TestCircuitBreakerForEndpoint:
    def test_get_circuit_breaker_from_registry(self):
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()
        breaker = registry.get_breaker("api_endpoint")

        assert breaker is not None
        assert breaker.state.value == "closed"


class TestPhase4Integration:
    def test_github_config_with_settings_service(self):
        from prdiffer.infrastructure.settings import SettingsService
        from prdiffer.domain.config.github_config import GitHubConfig

        service = SettingsService()
        config = service.get_github_config()

        assert isinstance(config, GitHubConfig)
        assert config.rate_limit > 0

    @pytest.mark.asyncio
    async def test_async_executor_with_circuit_breaker(self):
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )
        from prdiffer.infrastructure.utils.circuit_breaker_registry import (
            get_global_circuit_breaker_registry,
        )

        _reset_circuit_breaker_registry()

        registry = get_global_circuit_breaker_registry()

        async def protected_operation(x: int) -> int:
            endpoint = "test_api"
            if not registry.can_execute(endpoint):
                raise RuntimeError("Circuit breaker open")
            try:
                result = x * 2
                registry.record_success(endpoint)
                return result
            except Exception:
                registry.record_failure(endpoint)
                raise

        executor = AsyncParallelExecutor()
        results = await executor.execute_batch(protected_operation, [1, 2, 3, 4, 5])

        assert sorted(results) == [2, 4, 6, 8, 10]
        stats = registry.get_all_stats()
        assert stats["test_api"]["failure_count"] == 0

    def test_github_config_file_filtering(self):
        from prdiffer.domain.config.github_config import GitHubConfig

        config = GitHubConfig(
            ignore_patterns=("*.lock", "node_modules/", "*.min.js"),
            valid_extensions=(".py", ".js", ".ts", ".md"),
        )

        test_cases = [
            ("src/main.py", True),  # Valid Python file
            ("lib/index.js", True),  # Valid JS file
            ("package.lock", False),  # Ignored pattern
            ("node_modules/lib.js", False),  # Ignored directory
            ("bundle.min.js", False),  # Ignored minified
            ("style.css", False),  # Invalid extension
            ("README.md", True),  # Valid markdown
        ]

        for filename, expected in test_cases:
            result = config.should_process_file(filename)
            assert result == expected, f"Failed for {filename}: expected {expected}, got {result}"
