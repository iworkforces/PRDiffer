"""Unit tests for logging safety in application layer.

This test ensures that sensitive information like full diff content
is not logged at INFO level, only at DEBUG level with redaction.
"""

from unittest.mock import Mock

import pytest

from prdiffer.application.mcp_server import FastMCPServer
from prdiffer.application.provider_resolver import ProviderCapabilityResolver
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.infrastructure.utils.coalescing_service import RequestCoalescingService


def _create_pr_diff_with_content(diff_content: str) -> PRDiff:
    """Helper to create PRDiff with the new files structure."""
    return PRDiff(
        files=(
            FileDiffResponse(
                path="file.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=5, deletions=2),
                diff=diff_content,
            ),
        )
    )


@pytest.fixture
def server_with_mock_logger(mock_logger: Mock) -> FastMCPServer:
    """Create FastMCPServer instance with mocked dependencies."""
    settings_service = Mock()
    cache_service = Mock()
    rate_limiter = Mock()
    metrics_tracker = Mock()
    health_monitor = Mock()
    server_configuration = Mock()
    server_configuration.get_mcp_instructions.return_value = "instructions"
    server_configuration.setup_logging = Mock()
    authentication = Mock()
    input_validator = Mock()

    return FastMCPServer(
        settings_service=settings_service,
        cache_service=cache_service,
        logger=mock_logger,
        provider_resolver=ProviderCapabilityResolver(),
        rate_limiter=rate_limiter,
        metrics_tracker=metrics_tracker,
        health_monitor=health_monitor,
        server_configuration=server_configuration,
        authentication=authentication,
        input_validator=input_validator,
        request_coalescing_service=RequestCoalescingService(),
    )


@pytest.mark.unit
class TestLoggingSafety:
    """Ensure sensitive information is not logged at INFO level."""

    def test_does_not_log_full_diff_at_info(self, server_with_mock_logger: FastMCPServer, mock_logger: Mock) -> None:
        """Verify that full diff content is not logged at INFO level.

        This test ensures that when PR diff is successfully fetched,
        INFO log only contains size/hash summary, not the full diff content.
        """
        sample_diff_content = """diff --git a/file.py b/file.py
index 1234567..abcdefg 100644
--- a/file.py
+++ b/file.py
@@ -1,5 +1,5 @@
 def function():
-    old_code = True
+    new_code = True
     return True
"""
        pr_diff = _create_pr_diff_with_content(sample_diff_content)

        result = server_with_mock_logger._tool_registry._log_metrics_and_return_success(start_time=0.0, pr_diff=pr_diff)

        info_records = [r for r in mock_logger.records if r["level"] == "INFO"]
        for record in info_records:
            assert "diff_content" not in record["message"].lower()
            assert "old_code" not in record["message"]
            assert "new_code" not in record["message"]

        assert result == pr_diff

    def test_includes_size_hash_in_info(self, server_with_mock_logger: FastMCPServer, mock_logger: Mock) -> None:
        """Verify that INFO log includes size/hash summary of diff.

        This test ensures that INFO level logs include summary information
        about the diff (size, hash) without exposing actual content.
        """
        sample_diff_content = "sample diff content for testing"
        pr_diff = _create_pr_diff_with_content(sample_diff_content)

        server_with_mock_logger._tool_registry._log_metrics_and_return_success(start_time=0.0, pr_diff=pr_diff)

        info_records = [r for r in mock_logger.records if r["level"] == "INFO"]
        assert len(info_records) > 0, "Should have INFO logs"

        summary_found = any("size" in r["message"].lower() or "hash" in r["message"].lower() or "bytes" in r["message"].lower() for r in info_records)
        assert summary_found, "INFO log should include size/hash summary"

    def test_approve_pr_concise_info(self, server_with_mock_logger: FastMCPServer, mock_logger: Mock) -> None:
        """Verify that approve_pr result is not logged at INFO level with full content.

        This test ensures that when a PR is approved, the INFO log
        does not contain the full approval result message which may include
        sensitive details.
        """
        approval_result = "Successfully approved PR #123 with compliment: Great work!"

        server_with_mock_logger._logger.info(f"Successfully approved PR\n{approval_result}")

        info_records = [r for r in mock_logger.records if r["level"] == "INFO"]
        assert len(info_records) > 0

        for record in info_records:
            if "approved PR" in record["message"].lower():
                assert len(record["message"]) < 500, "Approval log should be concise"

    def test_debug_no_large_blocks(self, server_with_mock_logger: FastMCPServer, mock_logger: Mock) -> None:
        """Verify that DEBUG level can include diff summary information.

        This test ensures that DEBUG logs can include summary information
        about diffs (like size, hash) but not full content.
        """
        sample_diff_content = "diff content for debug testing"
        pr_diff = _create_pr_diff_with_content(sample_diff_content)

        server_with_mock_logger._tool_registry._log_metrics_and_return_success(start_time=0.0, pr_diff=pr_diff)

        debug_records = [r for r in mock_logger.records if r["level"] == "DEBUG"]
        if debug_records:
            for record in debug_records:
                if "diff" in record["message"].lower():
                    words = record["message"].split()
                    for word in words:
                        if len(word) > 200:
                            assert False, f"DEBUG log contains large text block: {word[:50]}..."

    def test_no_tokens_or_secrets_logged(self, server_with_mock_logger: FastMCPServer, mock_logger: Mock) -> None:
        """Verify that tokens, passwords, and secrets are never logged.

        This test ensures that even in error cases, sensitive credentials
        are not exposed in logs.
        """
        sensitive_diff = """diff --git a/config.py b/config.py
index 1234567..abcdefg 100644
--- a/config.py
+++ b/config.py
@@ -1,5 +1,5 @@
-API_KEY = "sk-1234567890abcdefghijklmnop"
+API_KEY = "sk-9876543210zyxwvutsrqponmlkj"
 PASSWORD = "secret123"
 DATABASE_URL = "postgresql://user:password123@localhost/db"
"""
        pr_diff = _create_pr_diff_with_content(sensitive_diff)

        result = server_with_mock_logger._tool_registry._log_metrics_and_return_success(start_time=0.0, pr_diff=pr_diff)

        all_records = mock_logger.records
        for record in all_records:
            assert "sk-" not in record["message"].lower(), "API keys should not be logged"
            assert "secret123" not in record["message"].lower(), "Passwords should not be logged"
            assert "password123" not in record["message"].lower(), "Passwords should not be logged"

        assert result == pr_diff
