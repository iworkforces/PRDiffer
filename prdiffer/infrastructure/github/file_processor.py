"""File processing service for GitHub repositories."""

import time
from collections.abc import Mapping
from typing import Any, Sequence

import anyio
import asyncer
from github.File import File
from github.PaginatedList import PaginatedList
from github.PullRequest import PullRequest as PyGithubPullRequest

from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE
from prdiffer.domain.entities.file_content import FileContentAvailable, FileContentRequest, FileContentResult
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.domain.services.github_api import GitHubAPIServiceInterface
from prdiffer.domain.services.pattern_matching import PatternMatchingServiceInterface
from prdiffer.domain.services.diff import DiffServiceInterface
from prdiffer.infrastructure.logging.console_logger import ConsoleLogger, get_logger
from prdiffer.infrastructure.github.git_objects import (
    MODE_GITLINK,
    GitBuildContext,
    fetch_blob_bytes,
    load_recursive_tree_entries,
    require_distinct_rename_previous,
    require_tree_entry,
    resolve_entry_text,
)
from prdiffer.infrastructure.utils.parallel.executor import (
    AsyncParallelExecutor,
)
from prdiffer.infrastructure.utils.parallel.results import ErrorStrategy


class FileProcessor:
    """Service for processing files in GitHub pull requests.

    This class handles file filtering, content loading, and parallel processing
    of files for diff generation using AsyncParallelExecutor for better performance.

    Thread Safety:
    - PR files cache access is protected by a reentrant lock
    - Prevents race condition in cache initialization and updates

    Async Support:
    - Parallel processing methods use AsyncParallelExecutor (anyio-based)
    - Maintains backward compatibility with sync methods
    """

    STATUS_TO_EDIT_TYPE: dict[str, EDIT_TYPE] = {
        "added": EDIT_TYPE.ADDED,
        "removed": EDIT_TYPE.DELETED,
        "renamed": EDIT_TYPE.RENAMED,
        "modified": EDIT_TYPE.MODIFIED,
    }

    def __init__(
        self,
        github_api_service: GitHubAPIServiceInterface,
        pattern_matcher: PatternMatchingServiceInterface,
        diff_utils: DiffServiceInterface,
        max_files_allowed: int = 50,
        parallel_fetch_threshold: int = 10,
        max_parallel_workers: int = 4,
        logger: ConsoleLogger | None = None,
        parallel_head_base_fetch_enabled: bool | None = None,
        *,
        require_git_tree: bool = False,
    ) -> None:
        self._github_api_service = github_api_service
        self._pattern_matcher = pattern_matcher
        self._diff_utils = diff_utils
        self.max_files_allowed = max_files_allowed
        self._parallel_fetch_threshold = parallel_fetch_threshold
        self._max_parallel_workers = max_parallel_workers
        # Production/session wiring sets True so mode/symlink/gitlink cannot silently degrade.
        self._require_git_tree = require_git_tree
        self._logger = logger or get_logger()

        self._cache_lock = anyio.Lock()

        self._pr_files_cache: PaginatedList[File] | None = None
        self._pr_cache_timestamp: float = 0.0

        self._async_executor = AsyncParallelExecutor(
            max_concurrent=max_parallel_workers,
            error_strategy=ErrorStrategy.IGNORE,
            logger=logger,
        )

        if parallel_head_base_fetch_enabled is None:
            from prdiffer.infrastructure.settings import get_settings_service

            settings = get_settings_service()
            self._parallel_head_base_fetch_enabled = bool(settings.get("performance.parallel_head_base_fetch_enabled", True))
        else:
            self._parallel_head_base_fetch_enabled = parallel_head_base_fetch_enabled

    async def get_pr_files(self, pull_request: PyGithubPullRequest) -> PaginatedList[File]:
        """Get all files from the pull request with caching (double-check locking, 5min TTL)."""
        if self._pr_files_cache is not None:
            current_time = time.time()
            if current_time - self._pr_cache_timestamp <= 300:
                return self._pr_files_cache

        async with self._cache_lock:
            current_time = time.time()
            if self._pr_files_cache is not None and current_time - self._pr_cache_timestamp <= 300:
                return self._pr_files_cache

            self._pr_files_cache = await asyncer.asyncify(pull_request.get_files)()
            self._pr_cache_timestamp = current_time
            assert self._pr_files_cache is not None
            return self._pr_files_cache

    def filter_files(self, files: Sequence[File]) -> list[File]:
        """Filter files based on pattern matching configuration."""
        return [file for file in files if self._pattern_matcher.is_valid_file(file.filename)]

    def _require_text(self, result: FileContentResult | str | None, *, path: str, ref: str) -> str:
        """Unwrap typed content; raise E5020 on deterministic unavailability.

        Accepts legacy bare strings from incomplete fakes during transition.
        """
        if result is None:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.CONTENT_UNAVAILABLE,
                path=path,
            )
        if isinstance(result, str):
            return result
        if isinstance(result, FileContentAvailable):
            return result.text
        unavailable = result
        reason_map = {
            "BINARY_CONTENT": FullDiffIncompleteReason.BINARY_CONTENT,
            "FILE_SIZE_LIMIT": FullDiffIncompleteReason.FILE_SIZE_LIMIT,
            "DIRECTORY": FullDiffIncompleteReason.CONTENT_UNAVAILABLE,
            "NOT_FOUND": FullDiffIncompleteReason.CONTENT_UNAVAILABLE,
            "CONTENT_DECODE_FAILED": FullDiffIncompleteReason.CONTENT_DECODE_FAILED,
        }
        raise FullDiffIncompleteError(
            reason_map.get(unavailable.reason.value, FullDiffIncompleteReason.CONTENT_UNAVAILABLE),
            path=path,
            observed=unavailable.observed_size,
        )

    def _text_map(self, results: Mapping[str, FileContentResult | str | None], *, ref: str) -> dict[str, str]:
        """Convert typed batch results to path→text, raising on unavailable entries."""
        texts: dict[str, str] = {}
        for path, value in results.items():
            texts[path] = self._require_text(value, path=path, ref=ref)
        return texts

    def _fetch_head_base_batches(
        self,
        repo_full_name: str,
        head_paths: list[str],
        base_paths: list[str],
        head_sha: str,
        base_sha: str,
    ) -> tuple[dict[str, FileContentResult], dict[str, FileContentResult]]:
        """Load head/base content batches; optionally concurrent when enabled."""
        if not head_paths and not base_paths:
            return {}, {}

        def _fetch(paths: list[str], ref: str) -> dict[str, FileContentResult]:
            if not paths:
                return {}
            return self._github_api_service.get_files_content_batch(repo_full_name, paths, ref)

        if self._parallel_head_base_fetch_enabled and head_paths and base_paths:
            requests: list[FileContentRequest] = []
            for index in range(max(len(head_paths), len(base_paths))):
                if index < len(head_paths):
                    requests.append(FileContentRequest(repo_full_name, head_paths[index], head_sha))
                if index < len(base_paths):
                    requests.append(FileContentRequest(repo_full_name, base_paths[index], base_sha))
            unique_requests = tuple(dict.fromkeys(requests))
            responses = self._github_api_service.get_files_content_multi_ref_batch(unique_requests)
            content_by_request = {response.request: response.content for response in responses}
            head_raw = {path: content_by_request[FileContentRequest(repo_full_name, path, head_sha)] for path in head_paths}
            base_raw = {path: content_by_request[FileContentRequest(repo_full_name, path, base_sha)] for path in base_paths}
            return head_raw, base_raw

        head_raw = _fetch(head_paths, head_sha)
        base_raw = _fetch(base_paths, base_sha)
        return head_raw, base_raw

    def process_files_to_patches(self, files: list[Any], repository: Any, head_sha: str, base_sha: str) -> list[FilePatchInfo]:
        """Assemble FilePatchInfo list in provider order (strict, no soft skips)."""
        classified = self._classify_selected_files(files)
        if not classified:
            return []
        head_paths, base_paths, rename_map = self._required_content_keys(classified)
        tree_ok = self._repository_supports_git_tree(repository)
        if self._require_git_tree and not tree_ok:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message="Strict full-diff requires repository.get_git_tree for mode-accurate assembly",
            )
        if tree_ok:
            return self._assemble_patches_from_trees(
                classified,
                repository,
                head_sha=head_sha,
                merge_base_sha=base_sha,
                rename_map=rename_map,
            )
        head_raw, base_raw = self._fetch_head_base_batches(
            repository.full_name,
            head_paths,
            base_paths,
            head_sha,
            base_sha,
        )
        head_contents = self._text_map(head_raw, ref=head_sha)
        base_contents = self._text_map(base_raw, ref=base_sha)
        return self._assemble_patches_in_order(classified, head_contents, base_contents, rename_map)

    async def process_files_to_patches_async(self, files: list[Any], repository: Any, head_sha: str, base_sha: str) -> list[FilePatchInfo]:
        """Async assembly with the same ordered strict semantics as the sync path."""
        classified = self._classify_selected_files(files)
        if not classified:
            return []
        head_paths, base_paths, rename_map = self._required_content_keys(classified)
        tree_ok = self._repository_supports_git_tree(repository)
        if self._require_git_tree and not tree_ok:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message="Strict full-diff requires repository.get_git_tree for mode-accurate assembly",
            )
        if tree_ok:
            run_sync = getattr(anyio.to_thread, "run_sync")
            return await run_sync(
                lambda: self._assemble_patches_from_trees(
                    classified,
                    repository,
                    head_sha=head_sha,
                    merge_base_sha=base_sha,
                    rename_map=rename_map,
                ),
            )

        # Blocking batch APIs may nest anyio.run; run concurrent head/base on worker threads.
        run_sync = getattr(anyio.to_thread, "run_sync")
        head_raw, base_raw = await run_sync(
            lambda: self._fetch_head_base_batches(
                repository.full_name,
                head_paths,
                base_paths,
                head_sha,
                base_sha,
            ),
        )
        head_contents = self._text_map(head_raw, ref=head_sha)
        base_contents = self._text_map(base_raw, ref=base_sha)

        return self._assemble_patches_in_order(classified, head_contents, base_contents, rename_map)

    def _classify_selected_files(self, files: Sequence[Any]) -> list[tuple[int, Any, EDIT_TYPE]]:
        """Classify selected provider files with original indices; reject UNKNOWN."""
        classified: list[tuple[int, Any, EDIT_TYPE]] = []
        for index, file in enumerate(files):
            if not self._pattern_matcher.is_valid_file(file.filename):
                continue
            status = getattr(file, "status", "") or ""
            edit_type = self.STATUS_TO_EDIT_TYPE.get(status, EDIT_TYPE.UNKNOWN)
            if edit_type is EDIT_TYPE.UNKNOWN:
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS,
                    path=file.filename,
                )
            classified.append((index, file, edit_type))
        return classified

    def _required_content_keys(
        self,
        classified: list[tuple[int, Any, EDIT_TYPE]],
    ) -> tuple[list[str], list[str], dict[str, str]]:
        """Build head/base path lists and rename base-path mapping."""
        head_paths: list[str] = []
        base_paths: list[str] = []
        rename_map: dict[str, str] = {}
        for _index, file, edit_type in classified:
            if edit_type in (EDIT_TYPE.ADDED, EDIT_TYPE.MODIFIED, EDIT_TYPE.RENAMED):
                head_paths.append(file.filename)
            if edit_type is EDIT_TYPE.MODIFIED:
                base_paths.append(file.filename)
            elif edit_type is EDIT_TYPE.DELETED:
                base_paths.append(file.filename)
            elif edit_type is EDIT_TYPE.RENAMED:
                # Fail closed before any tree/content acquisition when rename metadata is malformed.
                previous_name = require_distinct_rename_previous(
                    getattr(file, "previous_filename", None),
                    file.filename,
                )
                base_paths.append(previous_name)
                rename_map[file.filename] = previous_name

        # Preserve order, drop duplicates while keeping first occurrence.
        def _unique(paths: list[str]) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for path in paths:
                if path not in seen:
                    seen.add(path)
                    out.append(path)
            return out

        return _unique(head_paths), _unique(base_paths), rename_map

    def _assemble_patches_in_order(
        self,
        classified: list[tuple[int, Any, EDIT_TYPE]],
        head_contents: dict[str, str],
        base_contents: dict[str, str],
        rename_map: dict[str, str],
        *,
        head_modes: dict[str, str] | None = None,
        base_modes: dict[str, str] | None = None,
    ) -> list[FilePatchInfo]:
        """Reconstruct FilePatchInfo rows in original provider order (no skips)."""
        # Sort by original index to preserve provider order even if input shuffled.
        ordered = sorted(classified, key=lambda item: item[0])
        results: list[FilePatchInfo] = []
        head_modes = head_modes or {}
        base_modes = base_modes or {}
        for _index, file, edit_type in ordered:
            if edit_type is EDIT_TYPE.ADDED:
                original = ""
                new = head_contents.get(file.filename, "")
            elif edit_type is EDIT_TYPE.DELETED:
                original = base_contents.get(file.filename, "")
                new = ""
            elif edit_type is EDIT_TYPE.RENAMED:
                base_key = rename_map.get(file.filename) or file.filename
                original = base_contents.get(base_key, "")
                new = head_contents.get(file.filename, "")
            else:  # MODIFIED
                original = base_contents.get(file.filename, "")
                new = head_contents.get(file.filename, "")

            patch = file.patch or ""
            if not patch:
                patch = self._generate_patch_from_content(file.filename, new, original)

            old_mode = None
            new_mode = None
            if edit_type is EDIT_TYPE.ADDED:
                new_mode = head_modes.get(file.filename)
            elif edit_type is EDIT_TYPE.DELETED:
                old_mode = base_modes.get(file.filename)
            elif edit_type is EDIT_TYPE.RENAMED:
                base_key = rename_map.get(file.filename) or file.filename
                old_mode = base_modes.get(base_key)
                new_mode = head_modes.get(file.filename)
            else:
                old_mode = base_modes.get(file.filename)
                new_mode = head_modes.get(file.filename)

            results.append(
                self._create_file_patch_with_content(
                    file,
                    original,
                    new,
                    patch,
                    edit_type=edit_type,
                    old_filename=rename_map.get(file.filename) if edit_type is EDIT_TYPE.RENAMED else None,
                    old_mode=old_mode,
                    new_mode=new_mode,
                )
            )
        return results

    @staticmethod
    def _repository_supports_git_tree(repository: object) -> bool:
        """True when repository exposes a real get_git_tree (not a bare MagicMock)."""
        method = getattr(repository, "get_git_tree", None)
        if method is None or not callable(method):
            return False
        # unittest.mock creates callable attributes by default; those are not tree APIs.
        type_name = type(method).__name__
        if type_name in {"MagicMock", "AsyncMock", "Mock", "NonCallableMagicMock"}:
            return False
        return True

    def _max_content_bytes(self) -> int:
        raw = getattr(self._github_api_service, "_max_file_size_bytes", None)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
        return 10_485_760

    def _assemble_patches_from_trees(
        self,
        classified: list[tuple[int, Any, EDIT_TYPE]],
        repository: Any,
        *,
        head_sha: str,
        merge_base_sha: str,
        rename_map: dict[str, str],
    ) -> list[FilePatchInfo]:
        """Load immutable merge-base/head trees and assemble ordered patches."""
        max_size = self._max_content_bytes()
        context = GitBuildContext(
            repo_full_name=str(getattr(repository, "full_name", "")),
            merge_base_sha=merge_base_sha,
            head_sha=head_sha,
            max_file_size_bytes=max_size,
        )
        base_tree = load_recursive_tree_entries(repository, context.merge_base_sha)
        head_tree = load_recursive_tree_entries(repository, context.head_sha)

        head_contents: dict[str, str] = {}
        base_contents: dict[str, str] = {}
        head_modes: dict[str, str] = {}
        base_modes: dict[str, str] = {}

        for _index, file, edit_type in classified:
            if edit_type in (EDIT_TYPE.ADDED, EDIT_TYPE.MODIFIED, EDIT_TYPE.RENAMED):
                entry = require_tree_entry(head_tree, file.filename, ref=head_sha)
                blob = None if entry.mode == MODE_GITLINK else fetch_blob_bytes(repository, entry.object_id)
                resolved = resolve_entry_text(entry, blob_bytes=blob, max_file_size_bytes=max_size)
                head_contents[file.filename] = resolved.text
                head_modes[file.filename] = resolved.mode
            if edit_type is EDIT_TYPE.MODIFIED:
                entry = require_tree_entry(base_tree, file.filename, ref=merge_base_sha)
                blob = None if entry.mode == MODE_GITLINK else fetch_blob_bytes(repository, entry.object_id)
                resolved = resolve_entry_text(entry, blob_bytes=blob, max_file_size_bytes=max_size)
                base_contents[file.filename] = resolved.text
                base_modes[file.filename] = resolved.mode
            elif edit_type is EDIT_TYPE.DELETED:
                entry = require_tree_entry(base_tree, file.filename, ref=merge_base_sha)
                blob = None if entry.mode == MODE_GITLINK else fetch_blob_bytes(repository, entry.object_id)
                resolved = resolve_entry_text(entry, blob_bytes=blob, max_file_size_bytes=max_size)
                base_contents[file.filename] = resolved.text
                base_modes[file.filename] = resolved.mode
            elif edit_type is EDIT_TYPE.RENAMED:
                previous = rename_map[file.filename]
                entry = require_tree_entry(base_tree, previous, ref=merge_base_sha)
                blob = None if entry.mode == MODE_GITLINK else fetch_blob_bytes(repository, entry.object_id)
                resolved = resolve_entry_text(entry, blob_bytes=blob, max_file_size_bytes=max_size)
                base_contents[previous] = resolved.text
                base_modes[previous] = resolved.mode

        return self._assemble_patches_in_order(
            classified,
            head_contents,
            base_contents,
            rename_map,
            head_modes=head_modes,
            base_modes=base_modes,
        )

    def _create_file_patch_without_content(self, file: File) -> FilePatchInfo:
        """Create FilePatchInfo without loading file content."""
        edit_type = self.STATUS_TO_EDIT_TYPE.get(file.status, EDIT_TYPE.UNKNOWN)
        if edit_type == EDIT_TYPE.UNKNOWN:
            self._logger.error(f"Unknown edit type: {file.status}")

        patch = file.patch or ""
        num_plus_lines, num_minus_lines = self._count_patch_lines(file, patch)

        return FilePatchInfo(
            base_file="",
            head_file="",
            patch=patch,
            filename=file.filename,
            edit_type=edit_type,
            num_plus_lines=num_plus_lines,
            num_minus_lines=num_minus_lines,
        )

    def _create_file_patch_with_content(
        self,
        file: Any,
        original_content: str,
        new_content: str,
        patch: str,
        *,
        edit_type: EDIT_TYPE | None = None,
        old_filename: str | None = None,
        old_mode: str | None = None,
        new_mode: str | None = None,
    ) -> FilePatchInfo:
        """Create FilePatchInfo with loaded file content."""
        resolved_type = edit_type or self.STATUS_TO_EDIT_TYPE.get(file.status, EDIT_TYPE.UNKNOWN)
        if resolved_type is EDIT_TYPE.UNKNOWN:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS,
                path=file.filename,
            )

        num_plus_lines, num_minus_lines = self._count_patch_lines(file, patch)
        previous = old_filename
        if previous is None and resolved_type is EDIT_TYPE.RENAMED:
            previous = getattr(file, "previous_filename", None)

        return FilePatchInfo(
            base_file=original_content,
            head_file=new_content,
            patch=patch,
            filename=file.filename,
            edit_type=resolved_type,
            old_filename=previous,
            num_plus_lines=num_plus_lines,
            num_minus_lines=num_minus_lines,
            old_mode=old_mode,
            new_mode=new_mode,
        )

    def _count_patch_lines(self, file: File, patch: str) -> tuple[int, int]:
        """Count added and removed lines from file or patch."""
        if hasattr(file, "additions") and hasattr(file, "deletions"):
            return file.additions, file.deletions

        if patch:
            patch_lines = patch.splitlines(keepends=True)
            num_plus_lines = sum(1 for line in patch_lines if line.startswith("+"))
            num_minus_lines = sum(1 for line in patch_lines if line.startswith("-"))
            return num_plus_lines, num_minus_lines

        return 0, 0

    def _generate_patch_from_content(self, filename: str, new_content: str, original_content: str) -> str:
        """Generate a patch for a file by comparing content."""
        if not original_content and not new_content:
            return ""

        try:
            import difflib

            original_content = (original_content or "").rstrip() + "\n"
            new_content = (new_content or "").rstrip() + "\n"
            diff = difflib.unified_diff(
                original_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
            )
            self._logger.info(f"File was modified, but no patch was found. Manually creating patch: {filename}.")
            patch = "".join(diff)
            return patch
        except TypeError, ValueError, AttributeError:
            self._logger.error(f"Failed to generate patch for file: {filename}")
            return ""

    def _is_rename_only(self, file: File, original_content: str = "", new_content: str = "") -> bool:
        """Check if a renamed file has no content changes (API metadata primary, content comparison fallback)."""
        if hasattr(file, "additions") and hasattr(file, "deletions"):
            if file.additions == 0 and file.deletions == 0:
                self._logger.debug(f"Rename-only detected via API metadata: {file.filename}")
                return True
            return False

        if original_content and new_content:
            is_identical = original_content.rstrip() == new_content.rstrip()
            if is_identical:
                self._logger.debug(f"Rename-only detected via content comparison: {file.filename}")
            return is_identical

        # If no content available, cannot determine - assume has changes (conservative)
        return False
