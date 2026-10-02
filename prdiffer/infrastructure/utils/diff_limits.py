"""Helpers for enforcing full-diff size limits (strict rejection, no truncation)."""

from __future__ import annotations

from collections.abc import Sequence

from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason


def assert_diff_within_limit(diff_content: str, max_chars: int, *, path: str | None = None) -> None:
    """Reject a single public diff that exceeds the character limit.

    Boundary: len == max_chars succeeds; len == max_chars + 1 raises.
    """
    if max_chars <= 0:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT,
            path=path,
            observed=len(diff_content),
            limit=max_chars,
        )
    observed = len(diff_content)
    if observed > max_chars:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT,
            path=path,
            observed=observed,
            limit=max_chars,
        )


def assert_aggregate_within_limit(diffs: Sequence[str], max_total_chars: int) -> int:
    """Reject when the sum of public diffs exceeds max_total_chars.

    Returns the aggregate character count when within limit.
    """
    if max_total_chars <= 0:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT,
            observed=0,
            limit=max_total_chars,
        )
    total = 0
    for diff in diffs:
        total += len(diff)
        if total > max_total_chars:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT,
                observed=total,
                limit=max_total_chars,
            )
    return total
