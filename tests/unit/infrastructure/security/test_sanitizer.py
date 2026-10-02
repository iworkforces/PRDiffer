"""Tests for InputSanitizer."""

import pytest
from unittest.mock import Mock

from prdiffer.infrastructure.security.sanitizer import InputSanitizer
from prdiffer.domain.exceptions import InputSanitizationError, SuspiciousOperationError


@pytest.mark.unit
class TestSanitizeString:
    """Tests for InputSanitizer.sanitize_string classmethod."""

    def test_clean_string_passes(self):
        """Clean string is returned unchanged."""
        result = InputSanitizer.sanitize_string("hello world")
        assert result == "hello world"

    def test_empty_string_passes(self):
        """Empty string is valid."""
        result = InputSanitizer.sanitize_string("")
        assert result == ""

    def test_max_length_exceeded_raises(self):
        """String exceeding max_length raises InputSanitizationError."""
        with pytest.raises(InputSanitizationError, match="max 10 characters"):
            InputSanitizer.sanitize_string("a" * 11, max_length=10)

    def test_max_length_exact_passes(self):
        """String at exactly max_length passes."""
        result = InputSanitizer.sanitize_string("a" * 10, max_length=10)
        assert result == "a" * 10

    def test_null_bytes_raises(self):
        """String with null bytes raises InputSanitizationError."""
        with pytest.raises(InputSanitizationError, match="null bytes"):
            InputSanitizer.sanitize_string("hello\x00world")

    def test_suspicious_patterns_raises(self):
        """String with suspicious patterns raises SuspiciousOperationError."""
        with pytest.raises(SuspiciousOperationError, match="suspicious patterns"):
            InputSanitizer.sanitize_string("$(whoami)")

    def test_command_injection_raises(self):
        """Command injection pattern raises SuspiciousOperationError."""
        with pytest.raises(SuspiciousOperationError):
            InputSanitizer.sanitize_string("test; rm -rf /")

    def test_path_traversal_raises(self):
        """Path traversal raises SuspiciousOperationError."""
        with pytest.raises(SuspiciousOperationError):
            InputSanitizer.sanitize_string("../../../etc/passwd")

    def test_sql_injection_raises(self):
        """SQL injection pattern raises SuspiciousOperationError."""
        with pytest.raises(SuspiciousOperationError):
            InputSanitizer.sanitize_string("' OR '1'='1")

    def test_control_chars_stripped(self):
        """Control characters (except tab/newline/cr) are stripped."""
        result = InputSanitizer.sanitize_string("hello\x01\x02world")
        assert result == "helloworld"

    def test_tab_preserved(self):
        """Tab character is preserved in output."""
        result = InputSanitizer.sanitize_string("hello\tworld")
        assert result == "hello\tworld"

    def test_newline_preserved(self):
        """Newline character is preserved in output."""
        result = InputSanitizer.sanitize_string("hello\nworld")
        assert result == "hello\nworld"

    def test_carriage_return_preserved(self):
        """Carriage return is preserved in output."""
        result = InputSanitizer.sanitize_string("hello\rworld")
        assert result == "hello\rworld"

    def test_default_max_length_is_1000(self):
        """Default max_length allows up to 1000 characters."""
        result = InputSanitizer.sanitize_string("a" * 1000)
        assert len(result) == 1000

    def test_over_default_max_length_raises(self):
        """String over default 1000 characters raises."""
        with pytest.raises(InputSanitizationError):
            InputSanitizer.sanitize_string("a" * 1001)

    def test_consults_module_detector(self, monkeypatch):
        """sanitize_string checks input with the module-level injection detector."""
        mock_detector = Mock()
        mock_detector.check_suspicious_patterns.return_value = False
        monkeypatch.setattr("prdiffer.infrastructure.security.sanitizer._detector", mock_detector)

        result = InputSanitizer.sanitize_string("test input")

        assert result == "test input"
        mock_detector.check_suspicious_patterns.assert_called_once_with("test input")

    def test_detector_flag_blocks_input(self, monkeypatch):
        """A detector hit blocks otherwise innocuous input."""
        mock_detector = Mock()
        mock_detector.check_suspicious_patterns.return_value = True
        monkeypatch.setattr("prdiffer.infrastructure.security.sanitizer._detector", mock_detector)

        with pytest.raises(SuspiciousOperationError):
            InputSanitizer.sanitize_string("looks safe")

    def test_unicode_string_passes(self):
        """Unicode strings pass sanitization."""
        result = InputSanitizer.sanitize_string("héllo wörld 日本語")
        assert result == "héllo wörld 日本語"

    def test_printable_special_chars_pass(self):
        """Printable special characters (non-injection) pass."""
        result = InputSanitizer.sanitize_string("hello! @ [] {}")
        assert result == "hello! @ [] {}"


@pytest.mark.unit
class TestSanitizeForLogging:
    """Tests for InputSanitizer.sanitize_for_logging classmethod."""

    def test_short_string_unchanged(self):
        """Short string passes unchanged."""
        result = InputSanitizer.sanitize_for_logging("hello")
        assert result == "hello"

    def test_long_string_truncated(self):
        """Long string is truncated with ellipsis."""
        result = InputSanitizer.sanitize_for_logging("a" * 300)
        assert len(result) == 203  # 200 + '...'
        assert result.endswith("...")

    def test_custom_max_length(self):
        """Custom max_length controls truncation."""
        result = InputSanitizer.sanitize_for_logging("a" * 50, max_length=10)
        assert len(result) == 13  # 10 + '...'
        assert result.endswith("...")

    def test_exact_max_length_no_truncation(self):
        """String at exactly max_length is not truncated."""
        result = InputSanitizer.sanitize_for_logging("a" * 200, max_length=200)
        assert result == "a" * 200
        assert not result.endswith("...")

    def test_non_printable_replaced_with_question_mark(self):
        """Non-printable characters are replaced with ?."""
        result = InputSanitizer.sanitize_for_logging("hello\x00\x01world")
        assert result == "hello??world"

    def test_tab_preserved_in_logging(self):
        """Tab is preserved."""
        result = InputSanitizer.sanitize_for_logging("hello\tworld")
        assert result == "hello\tworld"

    def test_newline_preserved_in_logging(self):
        """Newline is preserved."""
        result = InputSanitizer.sanitize_for_logging("hello\nworld")
        assert result == "hello\nworld"

    def test_cr_preserved_in_logging(self):
        """Carriage return is preserved."""
        result = InputSanitizer.sanitize_for_logging("hello\rworld")
        assert result == "hello\rworld"

    def test_non_string_converted(self):
        """Non-string value is converted to string."""
        result = InputSanitizer.sanitize_for_logging(12345)
        assert result == "12345"

    def test_none_converted(self):
        """None is converted to string "None"."""
        result = InputSanitizer.sanitize_for_logging(None)
        assert result == "None"

    def test_empty_string(self):
        """Empty string passes."""
        result = InputSanitizer.sanitize_for_logging("")
        assert result == ""

    def test_default_max_length_is_200(self):
        """Default max_length is 200."""
        result = InputSanitizer.sanitize_for_logging("a" * 201)
        assert len(result) == 203  # 200 + '...'
