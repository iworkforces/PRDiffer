"""Synchronous python-gitlab MR operations and immutable diff version selection."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal, TypeGuard

import gitlab

from prdiffer.domain.error_codes import E1001_INVALID_URL, E5021_GITLAB_API_ERROR
from prdiffer.domain.exceptions import (
    FullDiffIncompleteError,
    FullDiffIncompleteReason,
    GitLabAPIError,
    HeadSHAMismatchError,
    ValidationError,
)
from prdiffer.infrastructure.vcs_providers.gitlab_models import (
    GitLabDiffRecord,
    GitLabDiffRefs,
    GitLabDiffSnapshot,
    GitLabVersionSummary,
)
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import (
    GitLabNotFoundContext,
    GitLabNotFoundKind,
    map_gitlab_exception,
)

_LOGGER = logging.getLogger(__name__)

# Per-request overrides that make one SDK call exactly one HTTP attempt. ``GitLabRuntime`` injects
# ``max_retries``/``obey_rate_limit`` with ``setdefault`` and sets the client-wide
# ``retry_transient_errors``; explicit per-request values win in ``Gitlab.http_request``.
_SINGLE_ATTEMPT_KWARGS: dict[str, Any] = {
    "retry_transient_errors": False,
    "max_retries": 0,
    "obey_rate_limit": False,
}
_HEAD_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_HTTP_CONFLICT = 409


class GitLabOperations:
    """Execute isolated synchronous GitLab operations with a provided SDK client.

    Strict full-diff path pins one MR diff version whose base/start/head SHAs
    exactly match the MR's current ``diff_refs``.

    Callers obtain the client via :class:`GitLabRuntime` (capacity, deadline,
    host allowlist); this class never opens or closes clients itself.
    """

    def select_with_client(
        self,
        client: gitlab.Gitlab,
        project_path: str,
        iid: int,
    ) -> GitLabDiffSnapshot:
        """Pin one MR diff version using an already-created SDK client.

        Exception mapping uses :func:`map_gitlab_exception` with project vs MR
        not-found context. Does not open or close the client.
        """
        try:
            project = client.projects.get(project_path)
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.PROJECT),
            )
            if mapped is not exc:
                raise mapped from None
            raise

        try:
            merge_request = project.mergerequests.get(iid)
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise

        try:
            refs = GitLabDiffRefs.from_mapping(getattr(merge_request, "diff_refs", None))
        except ValueError as exc:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message=f"MR diff_refs incomplete or malformed: {exc}",
            ) from None

        try:
            version_list = list(merge_request.diffs.list(get_all=True))
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise

        matches: list[GitLabVersionSummary] = []
        for item in version_list:
            try:
                summary = GitLabVersionSummary.from_object(item)
            except ValueError:
                # Skip unparsable list entries; selection still requires exact match
                continue
            if summary.matches_refs(refs):
                matches.append(summary)

        if len(matches) != 1:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message=(f"Expected exactly one MR diff version matching diff_refs; found {len(matches)}"),
                observed=len(matches),
                limit=1,
            )

        selected = matches[0]
        try:
            version = merge_request.diffs.get(selected.version_id)
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise

        try:
            fetched = GitLabVersionSummary.from_object(version)
        except ValueError as exc:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message=f"Fetched MR diff version metadata is malformed: {exc}",
            ) from None
        if fetched.version_id != selected.version_id or not fetched.matches_refs(refs):
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message="Fetched MR diff version id/refs drifted from selection",
            )

        state = str(getattr(version, "state", "") or "")
        real_size_raw = getattr(version, "real_size", None)
        try:
            real_size = parse_gitlab_real_size(real_size_raw)
        except ValueError:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message="MR diff version real_size is malformed",
            ) from None

        raw_diffs: object = getattr(version, "diffs", None)
        if raw_diffs is None:
            raw_diffs = []
        if not _is_object_list(raw_diffs):
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message="MR diff version diffs payload is malformed",
            )

        records: list[GitLabDiffRecord] = []
        for item in raw_diffs:
            try:
                record = item if _is_object_dict(item) else _as_dict(item)
                records.append(GitLabDiffRecord.from_mapping(record))
            except ValueError as exc:
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                    message=f"Malformed embedded diff record: {exc}",
                ) from None

        return GitLabDiffSnapshot(
            project_path=project_path,
            iid=iid,
            version_id=selected.version_id,
            base_sha=refs.base_sha,
            start_sha=refs.start_sha,
            head_sha=refs.head_sha,
            state=state,
            real_size=real_size,
            records=tuple(records),
        )

    def approve_with_client(
        self,
        client: gitlab.Gitlab,
        project_path: str,
        iid: int,
        compliment: str,
        *,
        expected_head_sha: str | None = None,
    ) -> str:
        """Approve an MR and post the compliment as a note (blocking SDK).

        Posts the note **before** calling approve so a note-API failure cannot leave
        the MR approved while the tool still returns an error (GitLab has no atomic
        approve-with-body call). If approve fails after the note lands, the note remains
        (visible feedback) and the mapped error is raised.

        With ``expected_head_sha`` the approval is head-bound: the freshly fetched MR
        ``sha`` must match before the note is created, ``approve`` receives ``sha=``, and
        every mutation is a single HTTP attempt. A definitive approval 409 deletes only
        the note this call created and raises :class:`HeadSHAMismatchError`.
        """
        if not isinstance(compliment, str) or not compliment.strip():
            raise ValidationError(
                "Compliment must be a non-empty string",
                error_code=E1001_INVALID_URL,
            )

        merge_request = self._get_merge_request(client, project_path, iid)
        if expected_head_sha is not None:
            self._approve_head_bound(merge_request, compliment, expected_head_sha)
            return f"Successfully approved MR !{iid} in {project_path}"

        try:
            # Note first: better partial state than "approved without compliment".
            merge_request.notes.create({"body": compliment})
            merge_request.approve()
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise

        return f"Successfully approved MR !{iid} in {project_path}"

    def _approve_head_bound(self, merge_request: Any, compliment: str, expected_head_sha: str) -> None:
        """Head-bound approve: compare ``sha`` pre-note, then note + ``approve(sha=)`` single-attempt."""
        current_head = _current_head_sha(merge_request)
        if current_head != expected_head_sha.casefold():
            raise HeadSHAMismatchError(expected_head_sha=expected_head_sha, actual_head_sha=current_head)

        try:
            note = merge_request.notes.create({"body": compliment}, **_SINGLE_ATTEMPT_KWARGS)
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise

        try:
            merge_request.approve(sha=expected_head_sha, **_SINGLE_ATTEMPT_KWARGS)
        except Exception as exc:
            if getattr(exc, "response_code", None) == _HTTP_CONFLICT:
                # Definitive "head moved": the approval was not recorded, so the note is orphaned.
                # Must run before map_gitlab_exception, which maps a bare 409 to E5021.
                raise HeadSHAMismatchError(
                    expected_head_sha=expected_head_sha,
                    compliment_note=_delete_compliment_note(note),
                ) from exc
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise

    def update_description_with_client(
        self,
        client: gitlab.Gitlab,
        project_path: str,
        iid: int,
        description: str,
    ) -> str:
        """Update an MR description field (blocking SDK)."""
        if not isinstance(description, str) or not description.strip():
            raise ValidationError(
                "PR description must be a non-empty string",
                error_code=E1001_INVALID_URL,
            )

        merge_request = self._get_merge_request(client, project_path, iid)
        try:
            merge_request.description = description
            merge_request.save()
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise

        return f"Successfully updated description for MR !{iid} in {project_path}"

    def _get_merge_request(self, client: gitlab.Gitlab, project_path: str, iid: int) -> Any:
        """Load project + MR with status-aware not-found mapping."""
        try:
            project = client.projects.get(project_path)
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.PROJECT),
            )
            if mapped is not exc:
                raise mapped from None
            raise

        try:
            return project.mergerequests.get(iid)
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise


def _current_head_sha(merge_request: object) -> str:
    """Return the MR head commit (``sha``, casefolded); fail closed when absent or malformed.

    ``diff_refs`` is diff-version metadata and may lag the source branch head, so it is not used.
    """
    head = getattr(merge_request, "sha", None)
    if not isinstance(head, str) or _HEAD_SHA_PATTERN.fullmatch(head) is None:
        raise GitLabAPIError(
            "GitLab merge request head SHA is missing or malformed",
            error_code=E5021_GITLAB_API_ERROR,
            details={"reason": "head_sha_unavailable"},
        )
    return head.casefold()


def _delete_compliment_note(note: Any) -> Literal["deleted", "cleanup_failed"]:
    """Delete exactly the note this call created (single attempt); never raises.

    A note without an id is not deleted: python-gitlab would address the notes collection.
    """
    note_id = getattr(note, "id", None)
    if note_id is None:
        _LOGGER.warning("Could not delete compliment note after approval head conflict: created note has no id")
        return "cleanup_failed"
    try:
        note.delete(**_SINGLE_ATTEMPT_KWARGS)
    except Exception as exc:
        status = getattr(exc, "response_code", None)
        _LOGGER.warning(
            "Could not delete compliment note %s after approval head conflict (%s, status=%s)",
            note_id,
            type(exc).__name__,
            status if isinstance(status, int) else None,
        )
        return "cleanup_failed"
    return "deleted"


def parse_gitlab_real_size(raw: object) -> int | None:
    """Parse MR diff-version ``real_size`` without boolean/float coercion.

    Accepts only a nonnegative non-boolean integer or an explicitly decimal digit
    string (e.g. ``\"1\"``). Rejects booleans, floats, negatives, empty/malformed
    strings, and other types.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValueError("boolean real_size is not allowed")
    if isinstance(raw, int):
        if raw < 0:
            raise ValueError("negative real_size is not allowed")
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.isdigit():
            return int(stripped)
        raise ValueError("malformed real_size string")
    raise ValueError(f"unsupported real_size type: {type(raw).__name__}")


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _as_dict(raw: object) -> dict[str, object]:
    as_dict = getattr(raw, "asdict", None)
    if callable(as_dict):
        data = as_dict()
        if _is_object_dict(data):
            return {str(k): v for k, v in data.items()}
    attributes = getattr(raw, "attributes", None)
    if _is_object_dict(attributes):
        return {str(k): v for k, v in attributes.items()}
    if _is_object_dict(raw):
        return {str(k): v for k, v in raw.items()}
    raise ValueError("cannot coerce diff record")
