"""Diff generation and patch processing service."""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, TypedDict

from prdiffer.domain.entities.file_patch import EDIT_TYPE, FilePatchInfo
from prdiffer.domain.entities.generated_file_diff import GeneratedFileDiff
from prdiffer.domain.error_codes import E5003_DIFF_GENERATION_ERROR
from prdiffer.domain.exceptions import (
    DiffGenerationError,
    FullDiffIncompleteError,
    FullDiffIncompleteReason,
)
from prdiffer.domain.services.diff import DiffServiceInterface
from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)


class HunkDict(TypedDict):
    """Typed dictionary for a parsed patch hunk."""

    header: str
    new_lines: list[str]
    old_lines: list[str]
    start1: int
    start2: int


class DiffGenerator:
    """Service for generating extended diffs and processing patches."""

    RE_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[ ]?(.*)")

    def __init__(
        self,
        diff_utils: DiffServiceInterface,
        parallel_executor: Any = None,
        parallel_enabled: bool = False,
        parallel_threshold: int = 3,
        max_workers: int = 4,
        logger: logging.Logger | None = None,
    ) -> None:
        self._diff_utils = diff_utils
        self._parallel_executor = parallel_executor
        # Parallel generation is enabled explicitly by factory/settings (not bare ctor).
        # ThreadPoolExecutor can use ThreadPool without an injected executor when enabled.
        self._parallel_enabled = bool(parallel_enabled)
        self._parallel_threshold = parallel_threshold
        self._max_workers = max(
            1,
            int(getattr(parallel_executor, "max_concurrent", None) or getattr(parallel_executor, "max_workers", None) or max_workers),
        )
        self._logger = logger or get_logger()

    def generate_ordered_file_diffs(self, diff_files: list[FilePatchInfo]) -> list[GeneratedFileDiff]:
        """Generate one full-context diff per selected file in provider order.

        Missing provider patches are recovered from base/head text. Strict:
        returns exactly ``len(diff_files)`` results or raises.
        Contract inability → E5020/DIFF_GENERATION_FAILED; unexpected defects → E5003.
        """
        if not diff_files:
            return []

        if self._parallel_enabled and len(diff_files) >= self._parallel_threshold:
            results = self._generate_ordered_file_diffs_parallel(diff_files)
        else:
            results = [self._generate_one_file_diff(index, file_patch) for index, file_patch in enumerate(diff_files)]

        if len(results) != len(diff_files):
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                message=f"Generated {len(results)} diffs for {len(diff_files)} selected files",
                observed=len(results),
                limit=len(diff_files),
            )
        for expected_index, generated in enumerate(results):
            if generated.index != expected_index:
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                    message="Generated diff index identity mismatch",
                    path=generated.path,
                    observed=generated.index,
                    limit=expected_index,
                )
        return results

    def _generate_ordered_file_diffs_parallel(self, diff_files: list[FilePatchInfo]) -> list[GeneratedFileDiff]:
        """Generate ordered diffs concurrently; preserve index identity and strict failure."""
        workers = min(self._max_workers, len(diff_files))
        ordered: list[GeneratedFileDiff | None] = [None] * len(diff_files)
        first_error: BaseException | None = None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._generate_one_file_diff, index, file_patch): index for index, file_patch in enumerate(diff_files)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    ordered[index] = future.result()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                    # Best-effort cancel remaining work; already-running tasks finish.
                    for pending in futures:
                        pending.cancel()

        if first_error is not None:
            raise first_error

        return [item for item in ordered if item is not None]

    def generate_extended_diff(self, diff_files: list[FilePatchInfo], add_line_numbers_to_hunks: bool = False) -> list[str]:
        """Generate extended diffs (legacy list[str] surface).

        Delegates to :meth:`generate_ordered_file_diffs` for completeness guarantees.
        ``add_line_numbers_to_hunks`` remains available for display-only formatting.
        """
        ordered = self.generate_ordered_file_diffs(diff_files)
        if not add_line_numbers_to_hunks:
            return [item.diff for item in ordered]

        formatted: list[str] = []
        for item, file_patch in zip(ordered, diff_files, strict=True):
            formatted.append(
                self._decouple_and_convert_to_hunks_with_lines_numbers(
                    item.diff,
                    file_patch,
                    is_first_file=(item.index == 0),
                )
            )
        return formatted

    def _generate_one_file_diff(self, index: int, file_patch: FilePatchInfo) -> GeneratedFileDiff:
        """Build a single identity-bearing full-context diff from base/head text."""
        if file_patch.edit_type is EDIT_TYPE.UNKNOWN:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS,
                path=file_patch.filename,
            )

        previous_path = file_patch.old_filename if file_patch.edit_type is EDIT_TYPE.RENAMED else None
        if file_patch.edit_type is EDIT_TYPE.RENAMED and not previous_path:
            previous_path = file_patch.filename

        base_text = file_patch.base_file or ""
        head_text = file_patch.head_file or ""
        if file_patch.edit_type is EDIT_TYPE.ADDED:
            base_text = ""
        elif file_patch.edit_type is EDIT_TYPE.DELETED:
            head_text = ""

        try:
            body = self._build_full_context_body(base_text, head_text, provider_patch=file_patch.patch or "")
        except FullDiffIncompleteError:
            raise
        except Exception as exc:
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(
                "Unexpected failure generating full-context diff",
                extra={**sanitized, "path": file_patch.filename},
            )
            raise DiffGenerationError(
                f"Unexpected algorithm/runtime error generating diff for {file_patch.filename}",
                error_code=E5003_DIFF_GENERATION_ERROR,
                details={"path": file_patch.filename},
            ) from exc

        # Stable header order: mode headers, then rename headers, then body.
        mode_header = self._mode_headers(file_patch)
        rename_header = ""
        if file_patch.edit_type is EDIT_TYPE.RENAMED and previous_path:
            rename_header = f"rename from {previous_path}\nrename to {file_patch.filename}\n"

        if rename_header or mode_header:
            # Rename/mode-only still emits deterministic headers when text is identical/empty.
            if base_text == head_text:
                body_part = body.lstrip("\n") if body.strip() else ""
                diff = (mode_header + rename_header + body_part).rstrip("\n")
            else:
                diff = mode_header + rename_header + body.lstrip("\n")
        else:
            diff = body

        return GeneratedFileDiff(
            index=index,
            path=file_patch.filename,
            previous_path=previous_path if file_patch.edit_type is EDIT_TYPE.RENAMED else None,
            diff=diff,
        )

    @staticmethod
    def _mode_headers(file_patch: FilePatchInfo) -> str:
        """Deterministic Git-style mode headers (add/delete/change).

        Supports regular (100644/100755), symlink (120000), and gitlink (160000).
        """
        old_mode = file_patch.old_mode
        new_mode = file_patch.new_mode
        edit = file_patch.edit_type
        if edit is EDIT_TYPE.ADDED and new_mode:
            return f"new file mode {new_mode}\n"
        if edit is EDIT_TYPE.DELETED and old_mode:
            return f"deleted file mode {old_mode}\n"
        if old_mode is not None and new_mode is not None and old_mode != new_mode:
            return f"old mode {old_mode}\nnew mode {new_mode}\n"
        return ""

    # Whole-body sentinels only (legacy builders). Do NOT substring-scan unified
    # text: real source may contain "DIFF TRUNCATED" / "[DIFF TRUNCATED]" (e.g.
    # settings.toml, tests) and must not trip E5020 RESPONSE_SIZE_LIMIT.
    _TRUNCATION_BODY_SENTINELS: frozenset[str] = frozenset(
        {
            "[DIFF TRUNCATED]",
            "DIFF TRUNCATED",
        }
    )

    def _build_full_context_body(self, base_text: str, head_text: str, *, provider_patch: str) -> str:
        """Build full-file unified body from base/head text only (never provider hunks)."""
        del provider_patch  # retained for call-site stability; never used as content source
        try:
            body = self._diff_utils.build_full_file_patch_chunked(base_text, head_text)
        except FullDiffIncompleteError:
            raise
        except Exception as exc:
            # Fail closed: never fall back to provider hunk text.
            raise DiffGenerationError(
                "Failed to reconstruct full-context diff from file text",
                error_code=E5003_DIFF_GENERATION_ERROR,
            ) from exc

        # Binary / pure-truncation are whole-response sentinels, not content scans.
        # Never substring-search for "DIFF TRUNCATED" — real source may contain that text.
        stripped = body.strip()
        if stripped.startswith("[BINARY FILE"):
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.BINARY_CONTENT,
                message="Binary content cannot produce a full-context text diff",
            )
        if stripped in self._TRUNCATION_BODY_SENTINELS:
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT,
                message="Diff builder produced a truncation marker instead of full context",
            )
        return body

    def _decouple_and_convert_to_hunks_with_lines_numbers(self, patch: str, file: FilePatchInfo, is_first_file: bool = False) -> str:
        """Convert a given patch string into a string with line numbers for each hunk."""
        patch_with_lines_str = self._generate_file_header(file, is_first_file)

        patch_lines = patch.splitlines()
        hunks = self._parse_hunks_from_patch(patch_lines)

        for hunk in hunks:
            patch_with_lines_str += self._format_hunk_with_line_numbers(hunk)

        return patch_with_lines_str.rstrip()

    def _generate_file_header(self, file: FilePatchInfo | None, is_first_file: bool) -> str:
        """Generate the file header for the patch output."""
        if not file:
            return ""

        separator = "" if is_first_file else "\n\n---"

        return f"{separator}\n## Full file path: `{file.filename.strip()}`\n"

    def _parse_hunks_from_patch(self, patch_lines: list[str]) -> list[HunkDict]:
        """Parse hunks from patch lines."""
        hunks: list[HunkDict] = []
        current_hunk: HunkDict | None = None
        RE_HUNK_HEADER = self.RE_HUNK_HEADER

        for line_i, line in enumerate(patch_lines):
            if "no newline at end of file" in line.lower():
                continue

            if line.startswith("@@"):
                if current_hunk is not None:
                    hunks.append(current_hunk)

                match = RE_HUNK_HEADER.match(line)
                if match:
                    _, _, _, start1, start2 = self._extract_hunk_headers(match)
                    current_hunk = {
                        "header": line,
                        "new_lines": [],
                        "old_lines": [],
                        "start1": start1,
                        "start2": start2,
                    }
            elif current_hunk is not None:
                self._add_line_to_hunk(current_hunk, line, line_i, patch_lines)

        if current_hunk is not None:
            hunks.append(current_hunk)

        return hunks

    def _add_line_to_hunk(
        self,
        hunk: HunkDict,
        line: str,
        line_i: int,
        patch_lines: list[str],
    ) -> None:
        """Add a line to the current hunk."""
        if line.startswith("+"):
            hunk["new_lines"].append(line)
        elif line.startswith("-"):
            hunk["old_lines"].append(line)
        else:
            if not line and line_i:
                if line_i + 1 < len(patch_lines) and patch_lines[line_i + 1].startswith("@@"):
                    return
                elif line_i + 1 == len(patch_lines):
                    return

            hunk["new_lines"].append(line)
            hunk["old_lines"].append(line)

    def _format_hunk_with_line_numbers(self, hunk: HunkDict) -> str:
        """Format a hunk with line numbers.

        Handles edge cases:
        - New files (start1=0): Uses start2 for line numbers
        - Files with only deletions: Shows old content appropriately
        - Empty hunks: Returns empty string
        """
        output = f"\n{hunk['header']}\n"

        has_additions = any(line.startswith("+") for line in hunk["new_lines"])
        has_deletions = any(line.startswith("-") for line in hunk["old_lines"])

        if not (has_additions or has_deletions):
            return ""  # No changes in this hunk

        is_deletion_only = has_deletions and not has_additions

        new_start_line = max(1, hunk["start2"])

        if not is_deletion_only:
            output = output.rstrip() + "\n__new hunk__\n"
            line_num = new_start_line
            new_lines_output: list[str] = []
            for line_new in hunk["new_lines"]:
                if not line_new.startswith("-"):
                    new_lines_output.append(f"{line_num} {line_new}")
                    line_num += 1
            output += "\n".join(new_lines_output)
            if new_lines_output:
                output += "\n"
        elif is_deletion_only and hunk["new_lines"]:
            output = output.rstrip() + "\n__new hunk__\n"
            line_num = new_start_line
            new_lines_output: list[str] = []
            for line_new in hunk["new_lines"]:
                if not line_new.startswith("-"):
                    new_lines_output.append(f"{line_num} {line_new}")
                    line_num += 1
            output += "\n".join(new_lines_output)
            if new_lines_output:
                output += "\n"

        if has_deletions:
            output = output.rstrip() + "\n__old hunk__\n"
            old_start_line = max(1, hunk["start1"])
            line_num = old_start_line
            old_lines_output: list[str] = []
            for line_old in hunk["old_lines"]:
                old_lines_output.append(f"{line_num} {line_old}")
                line_num += 1
            output += "\n".join(old_lines_output)
            if old_lines_output:
                output += "\n"

        return output

    def _extract_hunk_headers(self, match: re.Match[str]) -> tuple[str, int, int, int, int]:
        """Extract and parse hunk header information from regex match.

        Note:
            Handles edge cases:
            - '@@ -0,0 +1 @@' for new files (no original content)
            - '@@ -1 +0,0 @@' for deleted files (no new content)
            - '@@ -1,3 +1,3 @@' for standard modifications
            - Missing size values default to 1
        """
        res: list[str | None] = list(match.groups())

        # Groups: (start1, size1, start2, size2, section_header)
        # Pattern: @@ -(start1)(?:,(size1))? +(start2)(?:,(size2))? @@
        for i in range(4):  # Only process numeric fields
            if res[i] is None:
                res[i] = "1" if i in (1, 3) else "0"

        try:
            start1 = int(res[0] or "0")
            size1 = int(res[1] or "1")
            start2 = int(res[2] or "0")
            size2 = int(res[3] or "1")
        except (ValueError, IndexError) as e:
            self._logger.warning(f"Unexpected hunk header format: {e}")
            start1, size1, start2, size2 = 0, 0, 0, 0

        section_header: str = res[4] if len(res) > 4 and res[4] is not None else ""
        return section_header, size1, size2, start1, start2

    def _process_single_file_for_diff(self, indexed_file_data: tuple[int, FilePatchInfo, bool, int]) -> tuple[int, str] | None:
        """Process a single file for diff generation (worker function for parallel processing)."""
        i, file, add_line_numbers_to_hunks, _total_files = indexed_file_data

        try:
            original_file_content_str = file.base_file
            new_file_content_str = file.head_file
            patch = file.patch

            if not patch:
                return None

            extended_patch = self._diff_utils.extend_patch(original_file_content_str, patch, new_file_str=new_file_content_str)

            if not extended_patch:
                self._logger.warning(f"Failed to extend patch for file: {file.filename}")
                return None

            is_first_file = i == 0

            if add_line_numbers_to_hunks:
                full_extended_patch = self._decouple_and_convert_to_hunks_with_lines_numbers(extended_patch, file, is_first_file=is_first_file)
            else:
                separator = "" if is_first_file else "\n---"
                full_extended_patch = f"{separator}{'' if is_first_file else '\n\n'}## Full file path: `{file.filename.strip()}`\n{extended_patch.rstrip()}\n"
                if is_first_file:
                    full_extended_patch = f"\n{full_extended_patch}"

            return (i, full_extended_patch)

        except Exception as e:
            sanitized = sanitize_exception_for_logging(e)
            self._logger.error(f"Error processing file {file.filename} in parallel", extra=sanitized)
            return None

    def _generate_extended_diff_sequential(self, diff_files: list[FilePatchInfo], add_line_numbers_to_hunks: bool = False) -> list[str]:
        """Generate extended diff using sequential processing."""
        extended_diffs: list[str] = []
        for i, file in enumerate(diff_files):
            original_file_content_str = file.base_file
            new_file_content_str = file.head_file
            patch = file.patch
            if not patch:
                continue

            extended_patch = self._diff_utils.extend_patch(original_file_content_str, patch, new_file_str=new_file_content_str)
            if not extended_patch:
                self._logger.warning(f"Failed to extend patch for file: {file.filename}")
                continue

            if add_line_numbers_to_hunks:
                full_extended_patch = self._decouple_and_convert_to_hunks_with_lines_numbers(extended_patch, file, is_first_file=(i == 0))
            else:
                separator = "" if i == 0 else "\n---"
                full_extended_patch = f"{separator}{'' if i == 0 else '\n\n'}## Full file path: `{file.filename.strip()}`\n{extended_patch.rstrip()}\n"
                if i == 0:
                    full_extended_patch = f"\n{full_extended_patch}"
            extended_diffs.append(full_extended_patch)
        return extended_diffs

    def _generate_extended_diff_parallel(self, diff_files: list[FilePatchInfo], add_line_numbers_to_hunks: bool = False) -> list[str]:
        """Generate extended diff using parallel processing.

        Uses ParallelExecutor to process files concurrently,
        improving performance for PRs with many files.
        """
        if not self._parallel_executor:
            self._logger.warning("Parallel executor not available, falling back to sequential processing")
            return self._generate_extended_diff_sequential(diff_files, add_line_numbers_to_hunks)

        start_time = time.time()
        total_files = len(diff_files)

        indexed_files = [(i, file, add_line_numbers_to_hunks, total_files) for i, file in enumerate(diff_files)]

        self._logger.info(f"Starting parallel diff generation for {total_files} files using {self._parallel_executor.max_workers} workers")

        results = self._parallel_executor.execute_batch(self._process_single_file_for_diff, indexed_files)

        valid_results = [r for r in results if r is not None]
        valid_results.sort(key=lambda x: x[0])

        extended_diffs = [patch for _, patch in valid_results]

        elapsed_time = time.time() - start_time
        self._logger.info(
            f"Parallel diff generation completed: {len(extended_diffs)}/{total_files} files "
            f"processed in {elapsed_time:.2f}s "
            f"({elapsed_time / total_files * 1000:.1f}ms per file avg)"
        )

        return extended_diffs


def get_diff_generator(
    diff_utils: DiffServiceInterface,
    parallel_executor: Any = None,
    parallel_enabled: bool = False,
    parallel_threshold: int = 3,
    max_workers: int = 4,
) -> DiffGenerator:
    """Get a configured diff generator instance."""
    return DiffGenerator(
        diff_utils=diff_utils,
        parallel_executor=parallel_executor,
        parallel_enabled=parallel_enabled,
        parallel_threshold=parallel_threshold,
        max_workers=max_workers,
    )
