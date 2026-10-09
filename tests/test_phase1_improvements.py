"""Tests for Phase 1 improvements: Critical Fixes."""

import time
import threading
import pytest
import anyio
from unittest.mock import Mock, patch

from prdiffer.application.provider_resolver import ProviderCapabilityResolver
from prdiffer.infrastructure.security.input_validator import InputValidator
from prdiffer.infrastructure.utils.coalescing_service import RequestCoalescingService
from prdiffer.infrastructure.utils.circuit_breaker_core import (
    CircuitBreaker,
    CircuitState,
)
from prdiffer.infrastructure.utils.retry.handler import UnifiedRetryHandler
from prdiffer.domain.entities.pr_diff import PRDiff


class TestTTLExpiration:
    @pytest.fixture
    def sample_pr_diff(self):
        """Create sample PRDiff for testing."""
        return PRDiff(files=())

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_entry_not_expired_within_ttl(self, mock_get_settings, sample_pr_diff):
        from prdiffer.infrastructure.cache.service import CacheService

        mock_settings = Mock()
        mock_settings.get.side_effect = lambda key, default: {
            "cache.use_hashed_keys": False,
            "cache.ttl": 600,  # 10 minutes
        }.get(key, default)
        mock_get_settings.return_value = mock_settings

        cache_service = CacheService()
        cache_key = "owner/repo/pr/123"
        commit_sha = "abc123"

        await cache_service.set(cache_key, commit_sha, sample_pr_diff)

        result = await cache_service.get(cache_key, commit_sha)
        assert result == sample_pr_diff

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_entry_expired_after_ttl(self, mock_get_settings, sample_pr_diff):
        from prdiffer.infrastructure.cache.service import CacheService

        mock_settings = Mock()
        mock_settings.get.side_effect = lambda key, default: {
            "cache.use_hashed_keys": False,
            "cache.ttl": 1,  # 1 second TTL for testing
        }.get(key, default)
        mock_get_settings.return_value = mock_settings

        cache_service = CacheService()
        cache_key = "owner/repo/pr/123"
        commit_sha = "abc123"

        await cache_service.set(cache_key, commit_sha, sample_pr_diff)

        await anyio.sleep(1.5)

        result = await cache_service.get(cache_key, commit_sha)
        assert result is None

    @patch("prdiffer.infrastructure.settings.get_settings_service")
    @pytest.mark.asyncio
    async def test_expiration_statistics(self, mock_get_settings, sample_pr_diff):
        from prdiffer.infrastructure.cache.service import CacheService

        mock_settings = Mock()
        mock_settings.get.side_effect = lambda key, default: {
            "cache.use_hashed_keys": False,
            "cache.ttl": 1,
        }.get(key, default)
        mock_get_settings.return_value = mock_settings

        cache_service = CacheService()
        cache_key = "owner/repo/pr/123"
        commit_sha = "abc123"

        await cache_service.set(cache_key, commit_sha, sample_pr_diff)

        assert cache_service._cache_expirations == 0

        await anyio.sleep(1.5)

        await cache_service.get(cache_key, commit_sha)

        assert cache_service._cache_expirations == 1


class TestAsyncRetryHandler:
    @pytest.fixture
    def retry_handler(self):
        """Create retry handler with short delays for testing."""
        return UnifiedRetryHandler(max_retries=3, retry_delay=0.1)

    @pytest.mark.asyncio
    async def test_async_retry_success_first_attempt(self, retry_handler):
        call_count = 0

        async def successful_operation():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await retry_handler.execute_with_retry_async(successful_operation)

        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_retry_success_after_failures(self, retry_handler):
        call_count = 0

        async def eventually_successful():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("Connection timeout error")
            return "success"

        result = await retry_handler.execute_with_retry_async(eventually_successful)

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_async_retry_exhausted(self, retry_handler):

        async def always_fails():
            raise TimeoutError("Connection timeout error")

        with pytest.raises(TimeoutError, match="timeout"):
            await retry_handler.execute_with_retry_async(always_fails)

    @pytest.mark.asyncio
    async def test_async_retry_uses_anyio_sleep(self, retry_handler):
        call_count = 0

        async def fail_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("Connection timeout")
            return "success"

        start_time = time.time()
        result = await retry_handler.execute_with_retry_async(fail_once)
        elapsed = time.time() - start_time

        assert result == "success"
        assert elapsed >= 0.1


class TestThreadSafeCircuitBreaker:
    @pytest.fixture
    def circuit_breaker(self):
        """Create circuit breaker with low threshold for testing."""
        return CircuitBreaker(failure_threshold=3, timeout=1.0)

    def test_initial_state_is_closed(self, circuit_breaker):
        assert circuit_breaker.state == CircuitState.CLOSED
        permit = circuit_breaker.acquire()
        assert permit is not None
        circuit_breaker.release(permit)

    def test_opens_after_failure_threshold(self, circuit_breaker):
        for _ in range(3):
            permit = circuit_breaker.acquire()
            assert permit is not None
            circuit_breaker.record_failure(permit)

        assert circuit_breaker.state == CircuitState.OPEN
        assert circuit_breaker.acquire() is None

    def test_resets_on_success(self, circuit_breaker):
        for _ in range(2):
            permit = circuit_breaker.acquire()
            assert permit is not None
            circuit_breaker.record_failure(permit)
        assert circuit_breaker.failure_count == 2

        permit = circuit_breaker.acquire()
        assert permit is not None
        circuit_breaker.record_success(permit)
        assert circuit_breaker.failure_count == 0

    def test_transitions_to_half_open_after_timeout(self, circuit_breaker):
        with patch("prdiffer.infrastructure.utils.circuit_breaker_core.time.time", return_value=100.0) as clock:
            for _ in range(3):
                permit = circuit_breaker.acquire()
                assert permit is not None
                circuit_breaker.record_failure(permit)
            assert circuit_breaker.state == CircuitState.OPEN
            assert circuit_breaker.acquire() is None

            clock.return_value = 101.5

            permit = circuit_breaker.acquire()
            assert permit is not None
            assert circuit_breaker.state == CircuitState.HALF_OPEN
            assert circuit_breaker.acquire() is None
            circuit_breaker.release(permit)

    def test_thread_safety_concurrent_failures(self, circuit_breaker):
        threads = []
        errors = []

        def record_failures():
            try:
                for _ in range(10):
                    permit = circuit_breaker.acquire()
                    if permit is not None:
                        circuit_breaker.record_failure(permit)
            except Exception as e:
                errors.append(e)

        for _ in range(5):
            t = threading.Thread(target=record_failures)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert circuit_breaker.state == CircuitState.OPEN
        assert circuit_breaker.failure_count == 3

    def test_thread_safety_concurrent_successes(self, circuit_breaker):
        threads = []
        errors = []

        def record_successes():
            try:
                for _ in range(10):
                    permit = circuit_breaker.acquire()
                    assert permit is not None
                    circuit_breaker.record_success(permit)
            except Exception as e:
                errors.append(e)

        for _ in range(5):
            t = threading.Thread(target=record_successes)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0


class TestReDoSPatternFixes:
    def test_sql_keyword_detection_with_whitespace(self):
        validator = InputValidator()

        assert validator._detector.check_suspicious_patterns("select ")
        assert validator._detector.check_suspicious_patterns("union ")
        assert validator._detector.check_suspicious_patterns("drop ")

    def test_sql_keyword_detection_at_end(self):
        validator = InputValidator()

        assert validator._detector.check_suspicious_patterns("test select")
        assert validator._detector.check_suspicious_patterns("test union")

    def test_sql_keyword_not_detected_in_middle_of_word(self):
        validator = InputValidator()

        assert not validator._detector.check_suspicious_patterns("selector")
        assert not validator._detector.check_suspicious_patterns("reunion")
        assert not validator._detector.check_suspicious_patterns("dropdown")

    def test_windows_path_traversal_detection(self):
        validator = InputValidator()

        assert validator._detector.check_suspicious_patterns("C:\\Windows\\System32")
        assert validator._detector.check_suspicious_patterns("D:\\")

        assert validator._detector.check_suspicious_patterns("..\\config")

        assert validator._detector.check_suspicious_patterns("\\\\server\\share")

    def test_unix_path_traversal_detection(self):
        validator = InputValidator()

        assert validator._detector.check_suspicious_patterns("../etc/passwd")
        assert validator._detector.check_suspicious_patterns("~/")
        assert validator._detector.check_suspicious_patterns("/etc/passwd")

    def test_no_redos_vulnerability(self):
        validator = InputValidator()

        malicious_inputs = [
            "a" * 1000 + " select",  # Long string before keyword
            "select" + " " * 1000,  # Many spaces after keyword
            "union" + "\t" * 100,  # Many tabs
        ]

        start_time = time.time()

        for input_str in malicious_inputs:
            validator._detector.check_suspicious_patterns(input_str)

        elapsed = time.time() - start_time

        assert elapsed < 1.0, f"Pattern matching took too long: {elapsed}s"


class TestErrorMessageSanitization:
    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies for FastMCPServer."""
        return {
            "settings_service": Mock(),
            "cache_service": Mock(),
            "logger": Mock(),
            "provider_resolver": ProviderCapabilityResolver(),
            "input_validator": Mock(),
            "request_coalescing_service": RequestCoalescingService(),
            "rate_limiter": Mock(),
            "metrics_tracker": Mock(),
            "health_monitor": Mock(),
            "server_configuration": Mock(),
        }

    def test_github_exception_sanitization(self, mock_dependencies):
        from prdiffer.application.mcp_server import FastMCPServer

        mock_dependencies["server_configuration"].setup_logging = Mock()
        mock_dependencies["server_configuration"].get_mcp_instructions = Mock(return_value="")

        with patch("prdiffer.application.mcp_server.FastMCP"):
            server = FastMCPServer(**mock_dependencies)

        class GithubException(Exception):
            pass

        exc = GithubException("Detailed internal error: token xyz123 invalid")
        safe_message = server._tool_registry._create_safe_error_message(exc)

        assert safe_message == "GitHub API error occurred"
        assert "xyz123" not in safe_message
        assert "token" not in safe_message

    def test_rate_limit_exception_sanitization(self, mock_dependencies):
        from prdiffer.application.mcp_server import FastMCPServer

        mock_dependencies["server_configuration"].setup_logging = Mock()
        mock_dependencies["server_configuration"].get_mcp_instructions = Mock(return_value="")

        with patch("prdiffer.application.mcp_server.FastMCP"):
            server = FastMCPServer(**mock_dependencies)

        class RateLimitExceededException(Exception):
            pass

        exc = RateLimitExceededException("Rate limit: 5000/hour exceeded at 14:32:01")
        safe_message = server._tool_registry._create_safe_error_message(exc)

        assert safe_message == "API rate limit exceeded. Please try again later"

    def test_unknown_exception_sanitization(self, mock_dependencies):
        from prdiffer.application.mcp_server import FastMCPServer

        mock_dependencies["server_configuration"].setup_logging = Mock()
        mock_dependencies["server_configuration"].get_mcp_instructions = Mock(return_value="")

        with patch("prdiffer.application.mcp_server.FastMCP"):
            server = FastMCPServer(**mock_dependencies)

        class UnknownInternalError(Exception):
            pass

        exc = UnknownInternalError("Internal: database connection string is postgres://user:pass@host")
        safe_message = server._tool_registry._create_safe_error_message(exc)

        assert safe_message == "Request processing failed"
        assert "postgres" not in safe_message
        assert "user:pass" not in safe_message

    def test_security_exceptions_sanitization(self, mock_dependencies):
        from prdiffer.application.mcp_server import FastMCPServer
        from prdiffer.domain.exceptions import (
            InvalidURLError,
            SuspiciousOperationError,
        )

        mock_dependencies["server_configuration"].setup_logging = Mock()
        mock_dependencies["server_configuration"].get_mcp_instructions = Mock(return_value="")

        with patch("prdiffer.application.mcp_server.FastMCP"):
            server = FastMCPServer(**mock_dependencies)

        invalid_url_exc = InvalidURLError("URL contains malicious pattern: $(whoami)")
        safe_message = server._tool_registry._create_safe_error_message(invalid_url_exc)
        assert safe_message == "Invalid PR or merge request URL"
        assert "whoami" not in safe_message

        suspicious_exc = SuspiciousOperationError("Detected SQL injection: DROP TABLE users")
        safe_message = server._tool_registry._create_safe_error_message(suspicious_exc)
        assert safe_message == "Request contains suspicious patterns"
        assert "DROP TABLE" not in safe_message


class TestFileNameSanitization:
    def test_sanitize_for_logging_basic(self):
        validator = InputValidator()

        result = validator.sanitize_for_logging("src/main.py")
        assert result == "src/main.py"

        result = validator.sanitize_for_logging("tests/test_file.py")
        assert result == "tests/test_file.py"

    def test_sanitize_for_logging_truncation(self):
        validator = InputValidator()

        long_name = "a" * 300
        result = validator.sanitize_for_logging(long_name, max_length=200)

        assert len(result) <= 203  # 200 + "..."
        assert result.endswith("...")

    def test_sanitize_for_logging_control_characters(self):
        validator = InputValidator()

        malicious = "file\x00name\x07with\x1bcontrol.py"
        result = validator.sanitize_for_logging(malicious)

        assert "\x00" not in result
        assert "\x07" not in result
        assert "\x1b" not in result

    def test_sanitize_for_logging_newlines(self):
        validator = InputValidator()

        malicious = "file.py\nFake log entry: SUCCESS"
        result = validator.sanitize_for_logging(malicious)

        assert "Fake log entry" in result  # Content preserved


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
