"""Tests for E5020 full-diff incompleteness error contract."""

from __future__ import annotations

import pytest

from prdiffer.domain.error_codes import E5020_FULL_DIFF_INCOMPLETE
from prdiffer.domain.exceptions import (
    FullDiffIncompleteError,
    FullDiffIncompleteReason,
    GitHubAPIError,
)


EXPECTED_REASONS = (
    "INVENTORY_TRUNCATED",
    "FILE_COUNT_LIMIT",
    "BINARY_CONTENT",
    "FILE_SIZE_LIMIT",
    "CONTENT_UNAVAILABLE",
    "CONTENT_DECODE_FAILED",
    "UNSUPPORTED_FILE_STATUS",
    "DIFF_GENERATION_FAILED",
    "RESPONSE_SIZE_LIMIT",
    "SNAPSHOT_CHANGED",
)


class TestFullDiffIncompleteReason:
    def test_exact_reason_taxonomy(self) -> None:
        assert tuple(member.value for member in FullDiffIncompleteReason) == EXPECTED_REASONS
        assert len(FullDiffIncompleteReason) == 10


class TestE5020ErrorCode:
    def test_code_identity(self) -> None:
        assert E5020_FULL_DIFF_INCOMPLETE.code == "E5020"
        assert E5020_FULL_DIFF_INCOMPLETE.name == "FULL_DIFF_INCOMPLETE"
        assert str(E5020_FULL_DIFF_INCOMPLETE) == "E5020_FULL_DIFF_INCOMPLETE"

    def test_unique_among_error_code_constants(self) -> None:
        import prdiffer.domain.error_codes as codes

        all_codes = [value.code for name, value in vars(codes).items() if name.startswith("E") and hasattr(value, "code")]
        assert all_codes.count("E5020") == 1
        assert len(all_codes) == len(set(all_codes))


class TestFullDiffIncompleteError:
    def test_is_github_api_error_subclass(self) -> None:
        err = FullDiffIncompleteError(FullDiffIncompleteReason.BINARY_CONTENT, path="a.bin")
        assert isinstance(err, GitHubAPIError)
        assert err.error_code is E5020_FULL_DIFF_INCOMPLETE
        assert err.reason is FullDiffIncompleteReason.BINARY_CONTENT

    @pytest.mark.parametrize("reason", list(FullDiffIncompleteReason))
    def test_construct_each_reason(self, reason: FullDiffIncompleteReason) -> None:
        err = FullDiffIncompleteError(
            reason,
            path="src/a.py",
            previous_path="src/old.py",
            observed=10,
            limit=5,
        )
        assert err.details["reason"] == reason.value
        assert err.details["path"] == "src/a.py"
        assert err.details["previous_path"] == "src/old.py"
        assert err.details["observed"] == 10
        assert err.details["limit"] == 5
        assert str(err) == f"[E5020] Full diff incomplete: {reason.value} for src/a.py"

    def test_safe_structured_details_only(self) -> None:
        err = FullDiffIncompleteError(
            FullDiffIncompleteReason.FILE_COUNT_LIMIT,
            details={"observed": 51, "limit": 50},
        )
        assert set(err.details) <= {"reason", "path", "previous_path", "observed", "limit"}
        assert "token" not in err.details
        assert "content" not in err.details

    def test_rejects_token_in_details(self) -> None:
        with pytest.raises(ValueError, match="sensitive|token"):
            FullDiffIncompleteError(
                FullDiffIncompleteReason.CONTENT_UNAVAILABLE,
                details={"token": "ghp_secret", "path": "a.py"},
            )

    def test_rejects_raw_content_in_details(self) -> None:
        with pytest.raises(ValueError, match="sensitive|content"):
            FullDiffIncompleteError(
                FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                details={"raw_content": "print('secret')", "path": "a.py"},
            )

    def test_rejects_unknown_detail_keys(self) -> None:
        with pytest.raises(ValueError, match="unsupported keys"):
            FullDiffIncompleteError(
                FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT,
                details={"headers": {"Authorization": "Bearer x"}},
            )

    def test_custom_message_preserved(self) -> None:
        err = FullDiffIncompleteError(
            FullDiffIncompleteReason.INVENTORY_TRUNCATED,
            message="Authoritative inventory truncated",
            observed=3001,
            limit=3000,
        )
        assert str(err) == "[E5020] Authoritative inventory truncated"
        assert err.details["observed"] == 3001
        assert err.details["limit"] == 3000
