"""Ref-pinned typed file content acquisition for GitLab strict full-diff."""

from __future__ import annotations

from dataclasses import dataclass

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.entities.file_content import (
    FileContentAvailable,
    FileContentResult,
    FileContentUnavailable,
    FileContentUnavailableReason,
)
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.utils.parallel.executor import AsyncParallelExecutor
from prdiffer.infrastructure.utils.parallel.results import IndexedBatchError
from prdiffer.infrastructure.vcs_providers.gitlab_inventory import GitLabInventoryFile
from prdiffer.infrastructure.vcs_providers.gitlab_models import GitLabDiffSnapshot
from prdiffer.infrastructure.vcs_providers.gitlab_objects import (
    MODE_GITLINK,
    MODE_SYMLINK,
    fetch_raw_blob_bytes,
    load_repository_tree_entries,
    require_tree_entry,
    resolve_entry_text,
)
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import (
    GitLabNotFoundContext,
    GitLabNotFoundKind,
    GitLabRuntime,
    map_gitlab_exception,
)


@dataclass(frozen=True, slots=True)
class GitLabFileContents:
    """Typed base/head content for one admitted inventory file."""

    index: int
    path: str
    previous_path: str | None
    edit_type: EDIT_TYPE
    base: FileContentAvailable
    head: FileContentAvailable
    old_mode: str | None
    new_mode: str | None


class GitLabContentFetcher:
    """Fetch old/new raw content at immutable snapshot refs."""

    def __init__(
        self,
        runtime: GitLabRuntime,
        config: GitLabConfig,
        *,
        parallel_enabled: bool = True,
    ) -> None:
        self._runtime = runtime
        self._config = config
        capacity = config.max_concurrent if parallel_enabled else 1
        self._executor = AsyncParallelExecutor(max_concurrent=capacity)

    async def fetch_all(
        self,
        snapshot: GitLabDiffSnapshot,
        inventory: tuple[GitLabInventoryFile, ...],
        *,
        base_url: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> tuple[GitLabFileContents, ...]:
        """Fetch typed content for every admitted file in provider order.

        ``base_url`` and ``deadline_monotonic`` are per-request and forwarded to
        every SDK call so custom hosts and request deadlines apply uniformly.
        """
        if not inventory:
            return ()

        async def work(index: int) -> GitLabFileContents:
            return await self._fetch_one(
                snapshot,
                inventory[index],
                base_url=base_url,
                deadline_monotonic=deadline_monotonic,
            )

        indices = list(range(len(inventory)))
        try:
            batch = await self._executor.execute_indexed_batch(work, indices, strict=True)
        except IndexedBatchError as exc:
            # Prefer the first stable non-cancellation root (lowest index).
            first = exc.first_failure
            if first is not None and first.error is not None:
                raise first.error from None
            raise

        ordered: list[GitLabFileContents] = []
        for outcome in batch.outcomes:
            if outcome.error is not None:
                raise outcome.error
            if outcome.value is None:
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.CONTENT_UNAVAILABLE,
                    message="Content batch produced empty outcome",
                    observed=outcome.index,
                )
            ordered.append(outcome.value)
        if len(ordered) != len(inventory):
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.CONTENT_UNAVAILABLE,
                message="Content batch result count mismatch",
                observed=len(ordered),
                limit=len(inventory),
            )
        return tuple(ordered)

    async def _fetch_one(
        self,
        snapshot: GitLabDiffSnapshot,
        item: GitLabInventoryFile,
        *,
        base_url: str | None,
        deadline_monotonic: float | None,
    ) -> GitLabFileContents:
        record = item.record
        edit = item.edit_type
        path = record.new_path or record.old_path
        previous = record.old_path if edit is EDIT_TYPE.RENAMED else None

        if edit is EDIT_TYPE.ADDED:
            head = await self._content_for_side(
                snapshot.project_path,
                path=record.new_path,
                ref=snapshot.head_sha,
                mode=record.b_mode,
                base_url=base_url,
                deadline_monotonic=deadline_monotonic,
            )
            base = FileContentAvailable(text="")
        elif edit is EDIT_TYPE.DELETED:
            base = await self._content_for_side(
                snapshot.project_path,
                path=record.old_path,
                ref=snapshot.base_sha,
                mode=record.a_mode,
                base_url=base_url,
                deadline_monotonic=deadline_monotonic,
            )
            head = FileContentAvailable(text="")
        elif edit is EDIT_TYPE.RENAMED:
            base = await self._content_for_side(
                snapshot.project_path,
                path=record.old_path,
                ref=snapshot.base_sha,
                mode=record.a_mode,
                base_url=base_url,
                deadline_monotonic=deadline_monotonic,
            )
            head = await self._content_for_side(
                snapshot.project_path,
                path=record.new_path,
                ref=snapshot.head_sha,
                mode=record.b_mode,
                base_url=base_url,
                deadline_monotonic=deadline_monotonic,
            )
        else:
            # modified / mode-only
            old_path = record.old_path or record.new_path
            new_path = record.new_path or record.old_path
            base = await self._content_for_side(
                snapshot.project_path,
                path=old_path,
                ref=snapshot.base_sha,
                mode=record.a_mode,
                base_url=base_url,
                deadline_monotonic=deadline_monotonic,
            )
            head = await self._content_for_side(
                snapshot.project_path,
                path=new_path,
                ref=snapshot.head_sha,
                mode=record.b_mode,
                base_url=base_url,
                deadline_monotonic=deadline_monotonic,
            )

        return GitLabFileContents(
            index=item.index,
            path=path,
            previous_path=previous,
            edit_type=edit,
            base=base,
            head=head,
            old_mode=record.a_mode,
            new_mode=record.b_mode,
        )

    async def _content_for_side(
        self,
        project_path: str,
        *,
        path: str,
        ref: str,
        mode: str | None,
        base_url: str | None,
        deadline_monotonic: float | None,
    ) -> FileContentAvailable:
        """Fetch one side using mode-aware strategy (no raw-file for symlink/gitlink)."""
        if mode in {MODE_SYMLINK, MODE_GITLINK}:
            return await self._tree_object_content(
                project_path,
                path=path,
                ref=ref,
                expected_mode=mode,
                base_url=base_url,
                deadline_monotonic=deadline_monotonic,
            )
        return await self._raw(
            project_path,
            path,
            ref,
            required=True,
            base_url=base_url,
            deadline_monotonic=deadline_monotonic,
        )

    async def _tree_object_content(
        self,
        project_path: str,
        *,
        path: str,
        ref: str,
        expected_mode: str,
        base_url: str | None,
        deadline_monotonic: float | None,
    ) -> FileContentAvailable:
        """Resolve symlink/gitlink from immutable tree + raw blob (never files.raw)."""

        def callback(client: object) -> FileContentAvailable:
            projects = getattr(client, "projects")
            project = projects.get(project_path)
            tree = load_repository_tree_entries(project, ref=ref)
            entry = require_tree_entry(tree, path, ref=ref)
            if entry.mode != expected_mode:
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS,
                    path=path,
                    message=f"Tree mode {entry.mode} does not match inventory mode {expected_mode}",
                    observed=entry.mode,
                )
            blob: bytes | None = None
            if entry.mode != MODE_GITLINK:
                blob = fetch_raw_blob_bytes(project, entry.object_id)
            return resolve_entry_text(
                entry,
                blob_bytes=blob,
                max_file_size_bytes=self._config.max_file_size_bytes,
            )

        return await self._runtime.run_blocking(
            callback,
            not_found=GitLabNotFoundContext(GitLabNotFoundKind.FILE),
            base_url=base_url,
            deadline_monotonic=deadline_monotonic,
        )

    async def _raw(
        self,
        project_path: str,
        file_path: str,
        ref: str,
        *,
        required: bool,
        base_url: str | None,
        deadline_monotonic: float | None,
    ) -> FileContentAvailable:
        _ = required  # callers only request required sides; unavailable → E5020

        def callback(client: object) -> FileContentResult:
            projects = getattr(client, "projects")
            project = projects.get(project_path)
            try:
                raw = project.files.raw(file_path=file_path, ref=ref)
            except Exception as exc:
                status = getattr(exc, "response_code", None)
                if status == 404:
                    return FileContentUnavailable(
                        reason=FileContentUnavailableReason.NOT_FOUND,
                        path=file_path,
                        ref=ref,
                    )
                mapped = map_gitlab_exception(
                    exc,
                    not_found=GitLabNotFoundContext(GitLabNotFoundKind.FILE),
                )
                if mapped is not exc:
                    raise mapped from None
                raise

            if not isinstance(raw, (bytes, bytearray)):
                raw_bytes = bytes(raw) if raw is not None else b""
            else:
                raw_bytes = bytes(raw)

            size = len(raw_bytes)
            if size > self._config.max_file_size_bytes:
                return FileContentUnavailable(
                    reason=FileContentUnavailableReason.FILE_SIZE_LIMIT,
                    path=file_path,
                    ref=ref,
                    observed_size=size,
                )
            if b"\x00" in raw_bytes:
                return FileContentUnavailable(
                    reason=FileContentUnavailableReason.BINARY_CONTENT,
                    path=file_path,
                    ref=ref,
                    observed_size=size,
                )
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return FileContentUnavailable(
                    reason=FileContentUnavailableReason.CONTENT_DECODE_FAILED,
                    path=file_path,
                    ref=ref,
                    observed_size=size,
                )
            return FileContentAvailable(text=text)

        result = await self._runtime.run_blocking(
            callback,
            not_found=GitLabNotFoundContext(GitLabNotFoundKind.FILE),
            base_url=base_url,
            deadline_monotonic=deadline_monotonic,
        )

        if isinstance(result, FileContentAvailable):
            return result

        # Unavailable required content → E5020 (never operational 404 for missing required side)
        reason_map = {
            FileContentUnavailableReason.NOT_FOUND: FullDiffIncompleteReason.CONTENT_UNAVAILABLE,
            FileContentUnavailableReason.FILE_SIZE_LIMIT: FullDiffIncompleteReason.FILE_SIZE_LIMIT,
            FileContentUnavailableReason.BINARY_CONTENT: FullDiffIncompleteReason.BINARY_CONTENT,
            FileContentUnavailableReason.CONTENT_DECODE_FAILED: FullDiffIncompleteReason.CONTENT_DECODE_FAILED,
            FileContentUnavailableReason.DIRECTORY: FullDiffIncompleteReason.CONTENT_UNAVAILABLE,
        }
        e5020_reason = reason_map.get(result.reason, FullDiffIncompleteReason.CONTENT_UNAVAILABLE)
        raise FullDiffIncompleteError(
            e5020_reason,
            path=result.path,
            observed=result.observed_size,
            limit=self._config.max_file_size_bytes if result.reason is FileContentUnavailableReason.FILE_SIZE_LIMIT else None,
        )
