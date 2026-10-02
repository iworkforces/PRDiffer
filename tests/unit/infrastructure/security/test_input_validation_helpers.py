"""Tests for InputValidationHelpersMixin and convenience functions."""

import re
from unittest.mock import patch, MagicMock

import pytest

from prdiffer.domain.exceptions import (
    InputSanitizationError,
    SuspiciousOperationError,
)
from prdiffer.infrastructure.security.input_validation_helpers import (
    InputValidationHelpersMixin,
)


class ConcreteValidator(InputValidationHelpersMixin):
    """Concrete class with BRANCH_NAME_PATTERN for testing the mixin."""

    BRANCH_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\./]+$")

    def __init__(self):
        self._detector = MagicMock()
        self._detector.check_suspicious_patterns.return_value = False


# ---------------------------------------------------------------------------
# validate_token
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateToken:
    """Test InputValidationHelpersMixin.validate_token."""

    def test_valid_token(self):
        token = "ghp_" + "a" * 36
        result = ConcreteValidator.validate_token(token)
        assert result == token

    def test_empty_token_raises(self):
        with pytest.raises(InputSanitizationError, match="empty"):
            ConcreteValidator.validate_token("")

    def test_too_short_raises(self):
        with pytest.raises(InputSanitizationError, match="short"):
            ConcreteValidator.validate_token("abc")

    def test_too_long_raises(self):
        with pytest.raises(InputSanitizationError, match="long"):
            ConcreteValidator.validate_token("a" * 501)

    def test_whitespace_raises(self):
        token = " " + "a" * 30
        with pytest.raises(InputSanitizationError, match="whitespace"):
            ConcreteValidator.validate_token(token)

    def test_invalid_chars_raises(self):
        token = "ghp_" + "a" * 30 + "!"
        with pytest.raises(InputSanitizationError, match="invalid characters"):
            ConcreteValidator.validate_token(token)

    def test_non_string_raises(self):
        with pytest.raises(InputSanitizationError, match="string"):
            ConcreteValidator.validate_token(12345)


# ---------------------------------------------------------------------------
# validate_user_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateUserId:
    """Test InputValidationHelpersMixin.validate_user_id."""

    def test_valid_user_id(self):
        result = ConcreteValidator.validate_user_id("user-123")
        assert result == "user-123"

    def test_valid_email_format(self):
        result = ConcreteValidator.validate_user_id("user@example.com")
        assert result == "user@example.com"

    def test_empty_raises(self):
        with pytest.raises(InputSanitizationError, match="empty"):
            ConcreteValidator.validate_user_id("")

    def test_too_long_raises(self):
        with pytest.raises(InputSanitizationError, match="long"):
            ConcreteValidator.validate_user_id("u" * 101)

    def test_invalid_chars_raises(self):
        with pytest.raises(InputSanitizationError, match="invalid characters"):
            ConcreteValidator.validate_user_id("user;drop")

    def test_non_string_raises(self):
        with pytest.raises(InputSanitizationError, match="string"):
            ConcreteValidator.validate_user_id(42)


# ---------------------------------------------------------------------------
# validate_branch_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateBranchName:
    """Test InputValidationHelpersMixin.validate_branch_name."""

    def test_valid_branch(self):
        v = ConcreteValidator()
        result = v.validate_branch_name("feature/my-branch")
        assert result == "feature/my-branch"

    def test_simple_branch(self):
        v = ConcreteValidator()
        assert v.validate_branch_name("main") == "main"

    def test_empty_raises(self):
        with pytest.raises(InputSanitizationError, match="empty"):
            ConcreteValidator.validate_branch_name("")

    def test_too_long_raises(self):
        with pytest.raises(InputSanitizationError, match="long"):
            ConcreteValidator.validate_branch_name("b" * 256)

    def test_starts_with_slash_raises(self):
        v = ConcreteValidator()
        with pytest.raises(InputSanitizationError, match="start or end with '/'"):
            v.validate_branch_name("/branch")

    def test_ends_with_slash_raises(self):
        v = ConcreteValidator()
        with pytest.raises(InputSanitizationError, match="start or end with '/'"):
            v.validate_branch_name("branch/")

    def test_consecutive_slashes_raises(self):
        v = ConcreteValidator()
        with pytest.raises(InputSanitizationError, match="consecutive slashes"):
            v.validate_branch_name("feature//branch")

    def test_starts_with_dot_raises(self):
        v = ConcreteValidator()
        with pytest.raises(InputSanitizationError, match="start with '.'"):
            v.validate_branch_name(".hidden")

    def test_double_dot_raises(self):
        v = ConcreteValidator()
        # Patch the global _detector to let '..' pass through to the explicit check
        with patch("prdiffer.infrastructure.security.input_validation_helpers._detector") as mock_det:
            mock_det.check_suspicious_patterns.return_value = False
            with pytest.raises(SuspiciousOperationError, match="cannot contain"):
                v.validate_branch_name("feat..branch")

    def test_suspicious_patterns_detected(self):
        """When _detector finds suspicious patterns, raise SuspiciousOperationError."""
        v = ConcreteValidator()
        v._detector.check_suspicious_patterns.return_value = False
        # Use class-level detector mock via patching
        with patch("prdiffer.infrastructure.security.input_validation_helpers._detector") as mock_det:
            mock_det.check_suspicious_patterns.return_value = True
            with pytest.raises(SuspiciousOperationError, match="suspicious"):
                ConcreteValidator.validate_branch_name("evil-branch")


# ---------------------------------------------------------------------------
# sanitize_for_logging (classmethod)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSanitizeForLogging:
    """Test classmethod sanitize_for_logging delegates correctly."""

    def test_delegates_to_module_function(self):
        result = ConcreteValidator.sanitize_for_logging("hello world")
        assert isinstance(result, str)

    def test_truncates_long_values(self):
        long_val = "a" * 500
        result = ConcreteValidator.sanitize_for_logging(long_val, max_length=50)
        assert len(result) <= 55  # some overhead for truncation marker


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_validate_token_convenience(self):
        from prdiffer.infrastructure.security.input_validation_helpers import (
            validate_token as validate_token_fn,
        )

        token = "ghp_" + "x" * 36
        result = validate_token_fn(token)
        assert result == token

    def test_validate_user_id_convenience(self):
        from prdiffer.infrastructure.security.input_validation_helpers import (
            validate_user_id as validate_user_id_fn,
        )

        result = validate_user_id_fn("user123")
        assert result == "user123"

    def test_sanitize_string_convenience(self):
        from prdiffer.infrastructure.security.input_validation_helpers import (
            sanitize_string,
        )

        result = sanitize_string("hello")
        assert isinstance(result, str)

    def test_validate_pr_number_convenience(self):
        from prdiffer.infrastructure.security.input_validation_helpers import (
            validate_pr_number,
        )

        result = validate_pr_number(42)
        assert result == 42
