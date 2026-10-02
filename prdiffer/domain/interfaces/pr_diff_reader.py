"""Session-capable PR diff reader contracts (strict full-diff path)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import StrictPRDiffCacheIdentity

_GIT_OBJECT_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


def require_git_object_sha(value: object, *, field: str) -> str:
    """Require a nonempty 40- or 64-character lowercase-hex object id."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty git object SHA string")
    normalized = value.casefold()
    if _GIT_OBJECT_SHA_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field} must be a 40- or 64-character hexadecimal SHA")
    return normalized


def require_changed_files_count(value: object, *, field: str = "authoritative_changed_files") -> int:
    """Require a non-boolean nonnegative integer changed-file count."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a non-boolean integer")
    if value < 0:
        raise ValueError(f"{field} must be nonnegative")
    return value


@dataclass(frozen=True)
class PRDiffSnapshot:
    """Immutable PR comparison metadata captured once per request session.

    GitHub uses base-tip + merge-base + head. GitLab maps its pinned base into
    both tip/merge-base fields when only one base identity is available.
    """

    owner: str
    repo: str
    pr_number: int
    base_tip_sha: str
    merge_base_sha: str
    head_sha: str
    authoritative_changed_files: int

    def __post_init__(self) -> None:
        # Nonempty string identity fields for both providers. GitHub open path
        # additionally enforces 40/64-hex via ``require_git_object_sha``.
        for field in ("base_tip_sha", "merge_base_sha", "head_sha"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be a nonempty string")
        object.__setattr__(
            self,
            "authoritative_changed_files",
            require_changed_files_count(self.authoritative_changed_files),
        )


class PRDiffReadSessionInterface(Protocol):
    """Request-local session for inventory/content against one PR snapshot."""

    @property
    def snapshot(self) -> PRDiffSnapshot:
        """Immutable metadata for this open session."""
        ...

    @property
    def cache_identity(self) -> StrictPRDiffCacheIdentity:
        """Provider-neutral cache key + validation token for this session."""
        ...

    async def build_pr_diff(self) -> PRDiff:
        """Build a complete PRDiff using handles opened for this session."""
        ...

    async def aclose(self) -> None:
        """Release request-local resources (always called from finally)."""
        ...


@runtime_checkable
class SessionPRDiffReader(Protocol):
    """Structural capability: open a request-local PR diff session."""

    async def open_pr_diff_session(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ) -> PRDiffReadSessionInterface:
        """Open one session (one client/repo/PR metadata lookup).

        ``base_url`` is provider-specific (GitLab custom hosts). GitHub ignores it
        (callers pass ``None``). Implementations must accept the keyword even when unused.
        """
        ...
