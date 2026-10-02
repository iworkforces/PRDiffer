"""Strict full-diff size limit tests (no truncation success path)."""

import pytest

from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.utils.diff_limits import (
    assert_aggregate_within_limit,
    assert_diff_within_limit,
)


def test_per_file_exact_boundary_succeeds() -> None:
    content = "a" * 10
    assert_diff_within_limit(content, 10)


def test_per_file_plus_one_raises() -> None:
    content = "a" * 11
    with pytest.raises(FullDiffIncompleteError) as exc:
        assert_diff_within_limit(content, 10, path="x.py")
    assert exc.value.reason is FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT
    assert exc.value.details["observed"] == 11
    assert exc.value.details["limit"] == 10
    assert exc.value.details["path"] == "x.py"


def test_aggregate_exact_boundary_succeeds() -> None:
    diffs = ["a" * 40, "b" * 60]
    total = assert_aggregate_within_limit(diffs, 100)
    assert total == 100


def test_aggregate_plus_one_raises() -> None:
    diffs = ["a" * 40, "b" * 61]
    with pytest.raises(FullDiffIncompleteError) as exc:
        assert_aggregate_within_limit(diffs, 100)
    assert exc.value.reason is FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT
    assert exc.value.details["observed"] == 101
    assert exc.value.details["limit"] == 100
