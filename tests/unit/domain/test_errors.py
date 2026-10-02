"""Tests for structured error code types."""

import pytest

from prdiffer.domain.errors import ErrorCategory, ErrorCode
from prdiffer.domain.error_codes import E1001_INVALID_URL, E5001_INTERNAL_ERROR


# ---------------------------------------------------------------------------
# ErrorCategory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorCategory:
    """Test ErrorCategory enum."""

    def test_all_categories(self):
        assert ErrorCategory.INPUT_VALIDATION == "1"
        assert ErrorCategory.AUTHENTICATION == "2"
        assert ErrorCategory.RATE_LIMITING == "3"
        assert ErrorCategory.NOT_FOUND == "4"
        assert ErrorCategory.INTERNAL == "5"

    def test_is_str_enum(self):
        assert isinstance(ErrorCategory.INPUT_VALIDATION, str)


# ---------------------------------------------------------------------------
# ErrorCode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorCode:
    """Test ErrorCode frozen dataclass."""

    def test_create_error_code(self):
        code = ErrorCode(
            code="E9999",
            name="TEST",
            message="test msg",
            remediation="fix it",
            category=ErrorCategory.INTERNAL,
        )
        assert code.code == "E9999"
        assert code.name == "TEST"
        assert code.message == "test msg"
        assert code.remediation == "fix it"

    def test_str_format(self):
        code = ErrorCode(
            code="E1001",
            name="INVALID_URL",
            message="bad",
            remediation="fix",
            category=ErrorCategory.INPUT_VALIDATION,
        )
        assert str(code) == "E1001_INVALID_URL"

    def test_frozen_immutable(self):
        code = E1001_INVALID_URL
        with pytest.raises(AttributeError):
            setattr(code, "code", "E9999")

    def test_predefined_constants(self):
        assert E1001_INVALID_URL.code == "E1001"
        assert E5001_INTERNAL_ERROR.code == "E5001"
        assert E5001_INTERNAL_ERROR.category == ErrorCategory.INTERNAL
