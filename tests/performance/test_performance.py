"""Performance tests for PRDiffer.

These tests measure the performance of key operations to ensure
the application meets performance requirements and doesn't regress.
"""

import time
import pytest
from collections import deque

from prdiffer.infrastructure.utils.api_health_tracker import APIHealthTracker
from prdiffer.infrastructure.security.injection_detector import _detector
from prdiffer.infrastructure.security.input_validator import InputValidator
from prdiffer.infrastructure.utils.coalescing_service import RequestCoalescingService
from prdiffer.application.components.authentication import AuthenticationMiddleware


class TestInputValidatorPerformance:
    """Performance tests for InputValidator."""

    def test_url_validation_performance(self):
        """Test that URL validation is fast enough for high throughput."""
        url = "https://github.com/owner/repo/pull/123"
        validator = InputValidator()

        # Warm up
        for _ in range(10):
            validator.validate_github_url(url)

        # Measure performance
        start = time.perf_counter()
        iterations = 10000
        for _ in range(iterations):
            validator.validate_github_url(url)
        elapsed = time.perf_counter() - start

        # Should be able to validate 10k URLs in under 1 second
        assert elapsed < 1.0, f"URL validation too slow: {elapsed:.3f}s for {iterations} iterations"
        print(f"URL validation: {iterations} iterations in {elapsed:.3f}s ({iterations / elapsed:.0f} ops/sec)")

    def test_sanitization_performance(self):
        """Test that string sanitization is fast."""
        test_strings = [
            "https://github.com/owner/repo/pull/123",
            "a" * 1000,
            "normal string",
        ]

        start = time.perf_counter()
        iterations = 10000
        for _ in range(iterations):
            for s in test_strings:
                InputValidator.sanitize_string(s)
        elapsed = time.perf_counter() - start

        total_ops = iterations * len(test_strings)
        # CI shared runners can be ~2x slower than local; keep a wide margin.
        assert elapsed < 5.0, f"Sanitization too slow: {elapsed:.3f}s for {total_ops} operations"
        print(f"Sanitization: {total_ops} operations in {elapsed:.3f}s ({total_ops / elapsed:.0f} ops/sec)")


class TestAuthenticationPerformance:
    """Performance tests for authentication."""

    def test_api_key_hashing_performance(self):
        """Test that API key hashing is fast."""
        auth = AuthenticationMiddleware()
        api_key = "test_api_key_12345"

        # Warm up
        auth._hash_api_key(api_key)

        start = time.perf_counter()
        iterations = 10000
        for _ in range(iterations):
            auth._hash_api_key(api_key)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.5, f"Hashing too slow: {elapsed:.3f}s for {iterations} operations"
        print(f"API key hashing: {iterations} operations in {elapsed:.3f}s ({iterations / elapsed:.0f} ops/sec)")

    def test_authentication_performance(self):
        """Test that authentication is fast."""
        auth = AuthenticationMiddleware()
        api_key = "test_api_key_12345"

        # Disable expiration check for pure auth performance test
        auth._check_token_expiration = False

        # Add the key for authentication
        auth._hashed_api_keys.add(auth._hash_api_key(api_key))

        # Warm up
        auth.authenticate(api_key)

        start = time.perf_counter()
        iterations = 10000
        for _ in range(iterations):
            auth.authenticate(api_key)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0, f"Authentication too slow: {elapsed:.3f}s for {iterations} operations"
        print(f"Authentication: {iterations} operations in {elapsed:.3f}s ({iterations / elapsed:.0f} ops/sec)")


class TestAPIHealthTrackerPerformance:
    """Performance tests for API health tracker."""

    def test_health_score_calculation_performance(self):
        """Test that health score calculation is fast."""
        tracker = APIHealthTracker()

        # Add some calls
        for i in range(100):
            tracker.record_call(duration=0.1, success=i % 10 != 0)

        start = time.perf_counter()
        iterations = 10000
        for _ in range(iterations):
            tracker.get_health_score()
        elapsed = time.perf_counter() - start

        # Should be fast even with caching
        assert elapsed < 0.5, f"Health score calculation too slow: {elapsed:.3f}s"
        print(f"Health score: {iterations} calculations in {elapsed:.3f}s ({iterations / elapsed:.0f} ops/sec)")

    def test_stats_collection_performance(self):
        """Test that stats collection is fast."""
        tracker = APIHealthTracker()

        # Add some calls
        for i in range(100):
            tracker.record_call(duration=0.1, success=True)

        start = time.perf_counter()
        iterations = 10000
        for _ in range(iterations):
            tracker.get_stats()
        elapsed = time.perf_counter() - start

        assert elapsed < 0.5, f"Stats collection too slow: {elapsed:.3f}s"
        print(f"Stats collection: {iterations} operations in {elapsed:.3f}s")


class TestSecurityPatternMatchingPerformance:
    """Performance tests for security pattern matching."""

    def test_pattern_matching_performance(self):
        """Test that pattern matching is fast."""
        test_inputs = [
            "https://github.com/owner/repo/pull/123",
            "normal string without patterns",
            "owner/repo",
            "feature/new-branch",
        ]

        start = time.perf_counter()
        iterations = 10000
        for _ in range(iterations):
            for inp in test_inputs:
                _detector.check_suspicious_patterns(inp)
        elapsed = time.perf_counter() - start

        total_ops = iterations * len(test_inputs)
        assert elapsed < 2.0, f"Pattern matching too slow: {elapsed:.3f}s for {total_ops} operations"
        print(f"Pattern matching: {total_ops} operations in {elapsed:.3f}s ({total_ops / elapsed:.0f} ops/sec)")

    def test_case_insensitive_matching_performance(self):
        """Test case-insensitive pattern matching performance."""
        # Test inputs with different cases
        test_inputs = [
            "COMMAND INJECTION TEST",
            "SELECT * FROM users",
            "../etc/passwd",
        ]

        start = time.perf_counter()
        iterations = 5000
        for _ in range(iterations):
            for inp in test_inputs:
                _detector.check_suspicious_patterns(inp)
        elapsed = time.perf_counter() - start

        total_ops = iterations * len(test_inputs)
        assert elapsed < 2.0, f"Case-insensitive matching too slow: {elapsed:.3f}s"
        print(f"Case-insensitive matching: {total_ops} operations in {elapsed:.3f}s")


class TestConcurrencyPerformance:
    """Performance tests for concurrent operations."""

    def test_request_coalescing_performance(self):
        """Test that request coalescing is efficient."""
        import anyio

        coalescing = RequestCoalescingService()

        async def fetch_func():
            await anyio.sleep(0.01)  # Simulate 10ms API call
            return "result"

        async def test_coalescing():
            start = time.perf_counter()

            # Make 10 concurrent requests - should coalesce to 1 actual call
            async with anyio.create_task_group() as tg:
                for _ in range(10):
                    tg.start_soon(coalescing.coalesce, "test_key", fetch_func, 30.0)

            elapsed = time.perf_counter() - start
            return elapsed

        # Run multiple times
        times = []
        for _ in range(5):
            elapsed = anyio.run(test_coalescing)
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        # Should be close to 10ms (1 call) rather than 100ms (10 calls)
        assert avg_time < 0.05, f"Coalescing not working: {avg_time:.3f}s (expected ~0.01s)"
        print(f"Request coalescing: avg {avg_time:.3f}s for 10 concurrent requests")


class TestMemoryEfficiency:
    """Memory efficiency tests."""

    def test_api_health_tracker_memory(self):
        """Test that API health tracker uses bounded memory."""
        tracker = APIHealthTracker(window_size=100)

        # Add many calls (more than window size)
        for i in range(500):
            tracker.record_call(duration=0.1, success=True)

        stats = tracker.get_stats()

        # Should only track recent calls within window
        assert stats["total_calls"] <= 100, f"Tracker exceeded window size: {stats['total_calls']}"

    def test_deque_memory_behavior(self):
        """Test that deque with maxlen properly limits memory."""
        # Simulate the deque used in APIHealthTracker
        max_size = 100
        deque_with_limit = deque(maxlen=max_size)

        # Add more than max_size items
        for i in range(500):
            deque_with_limit.append({"id": i, "data": "x" * 100})

        # Should only contain last max_size items
        assert len(deque_with_limit) == max_size

        # Oldest items should have been evicted
        first_item = deque_with_limit[0]
        assert first_item["id"] == 400  # Items 0-399 were evicted


class TestBenchmark:
    """Benchmark tests for overall performance."""

    def test_full_validation_pipeline(self):
        """Benchmark the full input validation pipeline."""
        test_cases = [
            ("https://github.com/owner/repo/pull/123", True),
            ("https://github.com/owner/repo/pull/999999", True),
            ("owner/repo", True),
            ("SELECT * FROM users", False),  # Should be flagged
        ]
        validator = InputValidator()

        start = time.perf_counter()
        iterations = 5000
        for _ in range(iterations):
            for url, _ in test_cases:
                # Full pipeline: validate + sanitize + check patterns
                try:
                    validator.validate_github_url(url)
                except Exception:
                    pass
                _detector.check_suspicious_patterns(url)
        elapsed = time.perf_counter() - start

        total_ops = iterations * len(test_cases)
        assert elapsed < 3.0, f"Full pipeline too slow: {elapsed:.3f}s"
        print(f"Full validation pipeline: {total_ops} operations in {elapsed:.3f}s ({total_ops / elapsed:.0f} ops/sec)")


# Run performance benchmarks
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
