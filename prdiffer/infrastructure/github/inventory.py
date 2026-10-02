"""Authoritative PR file inventory validation and selected-file admission.

Strict completeness applies after configured ignore/extension selection.
Provider inventory must be proven complete before selection.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Protocol, TypeVar

from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason

# GitHub REST list-pull-request-files supports full pagination through 3000 files.
MAX_AUTHORITATIVE_CHANGED_FILES = 3000


class _NamedFile(Protocol):
    @property
    def filename(self) -> str: ...


_NamedFileT = TypeVar("_NamedFileT", bound=_NamedFile)


def materialize_pr_files(files: Iterable[_NamedFileT]) -> list[_NamedFileT]:
    """Fully enumerate provider file pages in source order."""
    return list(files)


def validate_authoritative_inventory(
    *,
    authoritative_changed_files: int,
    enumerated_count: int,
) -> None:
    """Reject truncated/mismatched inventories before any content loading.

    Rules:
    - Authoritative counts over 3000 fail with INVENTORY_TRUNCATED (before content).
    - Enumerated count must equal authoritative count (including both zero).
    """
    if authoritative_changed_files < 0 or enumerated_count < 0:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.INVENTORY_TRUNCATED,
            message="Negative inventory counts are invalid",
            observed=enumerated_count,
            limit=authoritative_changed_files,
        )

    if authoritative_changed_files > MAX_AUTHORITATIVE_CHANGED_FILES:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.INVENTORY_TRUNCATED,
            message=(f"Authoritative changed-file count {authoritative_changed_files} exceeds GitHub pagination ceiling {MAX_AUTHORITATIVE_CHANGED_FILES}"),
            observed=authoritative_changed_files,
            limit=MAX_AUTHORITATIVE_CHANGED_FILES,
        )

    if enumerated_count != authoritative_changed_files:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.INVENTORY_TRUNCATED,
            message=(f"Enumerated file count {enumerated_count} does not match authoritative changed_files {authoritative_changed_files}"),
            observed=enumerated_count,
            limit=authoritative_changed_files,
        )


def select_files_with_admission(
    files: Sequence[_NamedFileT],
    *,
    is_valid_file: Callable[[str], bool],
    max_files_allowed: int,
) -> list[_NamedFileT]:
    """Apply ignore/extension policy then enforce selected-file admission limit.

    Exactly N selected files succeeds; N+1 raises FILE_COUNT_LIMIT before content.
    """
    if isinstance(max_files_allowed, bool) or max_files_allowed <= 0:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.FILE_COUNT_LIMIT,
            message="max_files_allowed must be a positive integer",
            observed=0,
            limit=max_files_allowed,
        )

    selected: list[_NamedFileT] = [f for f in files if is_valid_file(f.filename)]
    if len(selected) > max_files_allowed:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.FILE_COUNT_LIMIT,
            message=(f"Selected file count {len(selected)} exceeds admission limit {max_files_allowed}"),
            observed=len(selected),
            limit=max_files_allowed,
        )
    return selected


def prepare_selected_inventory(
    *,
    authoritative_changed_files: int,
    provider_files: Iterable[_NamedFileT],
    is_valid_file: Callable[[str], bool],
    max_files_allowed: object,
) -> list[_NamedFileT]:
    """Materialize, validate inventory, then select with hard admission limit."""
    enumerated = materialize_pr_files(provider_files)
    validate_authoritative_inventory(
        authoritative_changed_files=authoritative_changed_files,
        enumerated_count=len(enumerated),
    )
    limit = max_files_allowed if isinstance(max_files_allowed, int) and not isinstance(max_files_allowed, bool) else 50
    selected = select_files_with_admission(
        enumerated,
        is_valid_file=is_valid_file,
        max_files_allowed=limit,
    )
    return list(selected)
