"""File processing service for GitHub repositories."""

from typing import Any, Sequence

from github.File import File

from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.domain.services.pattern_matching import PatternMatchingServiceInterface
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


class FileProcessor:
    """Assemble ordered full-content file patches for selected pull request files.

    Base and head text come from immutable git trees/blobs at the merge-base and
    head SHAs, preserving modes, renames, symlinks, and gitlinks.
    """

    STATUS_TO_EDIT_TYPE: dict[str, EDIT_TYPE] = {
        "added": EDIT_TYPE.ADDED,
        "removed": EDIT_TYPE.DELETED,
        "renamed": EDIT_TYPE.RENAMED,
        "modified": EDIT_TYPE.MODIFIED,
    }

    def __init__(
        self,
        pattern_matcher: PatternMatchingServiceInterface,
        max_files_allowed: int = 50,
        logger: ConsoleLogger | None = None,
        *,
        max_file_size_bytes: int = 10_485_760,
    ) -> None:
        self._pattern_matcher = pattern_matcher
        self.max_files_allowed = max_files_allowed
        self._max_file_size_bytes = max_file_size_bytes
        self._logger = logger or get_logger()

    def process_files_to_patches(self, files: list[Any], repository: Any, head_sha: str, base_sha: str) -> list[FilePatchInfo]:
        """Assemble FilePatchInfo list in provider order (strict, no soft skips)."""
        classified = self._classify_selected_files(files)
        if not classified:
            return []
        rename_map = self._rename_map(classified)
        return self._assemble_patches_from_trees(
            classified,
            repository,
            head_sha=head_sha,
            merge_base_sha=base_sha,
            rename_map=rename_map,
        )

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

    def _rename_map(self, classified: list[tuple[int, Any, EDIT_TYPE]]) -> dict[str, str]:
        """Map renamed head paths to their validated previous (base) paths."""
        rename_map: dict[str, str] = {}
        for _index, file, edit_type in classified:
            if edit_type is EDIT_TYPE.RENAMED:
                # Fail closed before any tree/content acquisition when rename metadata is malformed.
                rename_map[file.filename] = require_distinct_rename_previous(
                    getattr(file, "previous_filename", None),
                    file.filename,
                )
        return rename_map

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
        max_size = self._max_file_size_bytes
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
