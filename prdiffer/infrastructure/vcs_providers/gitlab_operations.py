"""Synchronous python-gitlab lifecycle and immutable MR diff version selection."""

from __future__ import annotations

from typing import Any, TypeGuard

import gitlab
import requests

from prdiffer.domain.error_codes import (
    E1001_INVALID_URL,
    E5019_CONNECTION_ERROR,
)
from prdiffer.domain.exceptions import (
    FullDiffIncompleteError,
    FullDiffIncompleteReason,
    InvalidURLError,
    PRDifferException,
    ValidationError,
)
from prdiffer.infrastructure.vcs_providers.gitlab_models import (
    GitLabDiffRecord,
    GitLabDiffRefs,
    GitLabDiffSnapshot,
    GitLabVersionSummary,
)
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import (
    GITLAB_COM_URL,
    GitLabNotFoundContext,
    GitLabNotFoundKind,
    cache_host_from_base_url,
    map_gitlab_exception,
)


class GitLabOperations:
    """Execute isolated synchronous GitLab operations with a provided SDK client.

    Strict full-diff path pins one MR diff version whose base/start/head SHAs
    exactly match the MR's current ``diff_refs``.

    Production callers must obtain the client via :class:`GitLabRuntime`
    (capacity, deadline, host allowlist). Synchronous helpers that open their
    own clients exist only for isolated unit tests and initialization probes.
    """

    def __init__(
        self,
        gitlab_token: str | None = None,
        *,
        base_url: str = GITLAB_COM_URL,
        allowed_hosts: tuple[str, ...] = ("gitlab.com",),
    ) -> None:
        self._gitlab_token = gitlab_token
        self._base_url = (base_url or GITLAB_COM_URL).rstrip("/")
        self._allowed_hosts = tuple(h.casefold() for h in allowed_hosts) or ("gitlab.com",)

    def _ensure_host_allowed(self, base_url: str) -> None:
        """Reject token-bearing client construction outside the host allowlist."""
        host = cache_host_from_base_url(base_url).split(":", 1)[0]
        if host not in self._allowed_hosts:
            raise InvalidURLError(
                f"GitLab host {host!r} is not in allowed_hosts {list(self._allowed_hosts)!r}",
                error_code=E1001_INVALID_URL,
                details={"host": host},
            )

    def initialize(self, *, base_url: str | None = None) -> None:
        """Authenticate a newly created GitLab client (blocking probe)."""
        url = (base_url or self._base_url).rstrip("/")
        self._ensure_host_allowed(url)
        try:
            with gitlab.Gitlab(url=url, private_token=self._gitlab_token) as client:
                client.auth()
        except gitlab.GitlabError, requests.RequestException:
            raise PRDifferException(
                "Failed to initialize GitLab connection",
                error_code=E5019_CONNECTION_ERROR,
            ) from None

    def get_latest_commit_sha(self, owner: str, repo: str, pr: int, *, base_url: str | None = None) -> str:
        """Return the head SHA from a pinned snapshot (compat surface)."""
        return self.select_diff_snapshot(f"{owner}/{repo}", pr, base_url=base_url).head_sha

    def get_diff_records(self, owner: str, repo: str, pr: int, *, base_url: str | None = None) -> tuple[GitLabDiffRecord, ...]:
        """Return ordered records from the pinned immutable version (compat)."""
        return self.select_diff_snapshot(f"{owner}/{repo}", pr, base_url=base_url).records

    def select_diff_snapshot(
        self,
        project_path: str,
        iid: int,
        *,
        base_url: str | None = None,
    ) -> GitLabDiffSnapshot:
        """Select and fetch exactly one MR diff version matching current diff_refs.

        Prefer :meth:`select_with_client` under :meth:`GitLabRuntime.run_blocking`
        on the request path so capacity and deadlines apply. This method opens a
        short-lived client for unit tests and non-session callers.
        """
        url = (base_url or self._base_url).rstrip("/")
        self._ensure_host_allowed(url)
        try:
            with gitlab.Gitlab(url=url, private_token=self._gitlab_token) as client:
                return self.select_with_client(client, project_path, iid)
        except FullDiffIncompleteError, InvalidURLError:
            raise
        except Exception as exc:
            mapped = map_gitlab_exception(
                exc,
                not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            )
            if mapped is not exc:
                raise mapped from None
            raise

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

        fetched = GitLabVersionSummary.from_object(version)
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
    ) -> str:
        """Approve an MR and post the compliment as a note (blocking SDK).

        Posts the note **before** calling approve so a note-API failure cannot leave
        the MR approved while the tool still returns an error (GitLab has no atomic
        approve-with-body call). If approve fails after the note lands, the note remains
        (visible feedback) and the mapped error is raised.
        """
        if not isinstance(compliment, str) or not compliment.strip():
            raise ValidationError(
                "Compliment must be a non-empty string",
                error_code=E1001_INVALID_URL,
            )

        merge_request = self._get_merge_request(client, project_path, iid)
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
