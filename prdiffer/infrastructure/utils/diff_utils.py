"""Diff utility for patch generation and manipulation."""

import difflib
import re
import logging
from dataclasses import dataclass
from prdiffer.domain.services.diff import DiffServiceInterface
from prdiffer.infrastructure.utils.logger_factory import LazyLoggerMixin


DEFAULT_LARGE_FILE_THRESHOLD = 5000  # Lines
DEFAULT_DIFF_CHUNK_SIZE = 1000  # Lines per chunk
DEFAULT_MAX_DIFF_SIZE = 100000  # Maximum diff size (100K lines)


@dataclass(frozen=True)
class DiffProcessingConfig:
    """Configuration for diff processing of large files."""

    large_file_threshold: int = DEFAULT_LARGE_FILE_THRESHOLD
    chunk_size: int = DEFAULT_DIFF_CHUNK_SIZE
    max_diff_size: int = DEFAULT_MAX_DIFF_SIZE

    def validate(self) -> "DiffProcessingConfig":
        """Validate configuration and return a corrected version if needed."""
        return DiffProcessingConfig(
            large_file_threshold=max(100, min(50000, self.large_file_threshold)),
            chunk_size=max(100, min(10000, self.chunk_size)),
            max_diff_size=max(1000, min(1000000, self.max_diff_size)),
        )


_NO_NEWLINE_MARKER = "\\ No newline at end of file"


def _lines_and_final_newline(text: str) -> tuple[list[str], bool]:
    """Split content into logical lines and report whether a final newline is present.

    ``splitlines()`` drops terminator information; callers use the boolean to emit
    Git-style ``\\ No newline at end of file`` markers. Empty content is treated as
    having no missing-final-newline marker (no lines to annotate).
    """
    if text == "":
        return [], True
    return text.splitlines(), text.endswith("\n")


def _append_no_newline_markers(
    body_lines: list[str],
    *,
    orig_ends_with_newline: bool,
    new_ends_with_newline: bool,
    orig_count: int,
    new_count: int,
) -> list[str]:
    """Insert Git no-newline markers after the last old/new-side body lines."""
    if not body_lines:
        return body_lines

    last_old: int | None = None
    last_new: int | None = None
    for index, line in enumerate(body_lines):
        if line.startswith(("-", " ")):
            last_old = index
        if line.startswith(("+", " ")):
            last_new = index

    inserts: list[tuple[int, str]] = []
    if not orig_ends_with_newline and orig_count > 0 and last_old is not None:
        inserts.append((last_old + 1, _NO_NEWLINE_MARKER))
    if not new_ends_with_newline and new_count > 0 and last_new is not None:
        inserts.append((last_new + 1, _NO_NEWLINE_MARKER))

    # Insert from the end so earlier indices stay valid. Same index (shared
    # context line for both sides) gets a single marker.
    inserts.sort(key=lambda item: item[0], reverse=True)
    result = list(body_lines)
    seen_indices: set[int] = set()
    for index, marker in inserts:
        if index in seen_indices:
            continue
        seen_indices.add(index)
        result.insert(index, marker)
    return result


class DiffUtils(LazyLoggerMixin, DiffServiceInterface):
    """Utility for diff generation, patch extension, and content decoding.

    This class provides functionality for creating unified diffs, extending
    patches with full context, and handling content encoding issues.
    """

    RE_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[ ]?(.*)")

    def __init__(self, logger: logging.Logger | None = None, config: DiffProcessingConfig | None = None) -> None:
        self._init_lazy_logger(logger, __name__)
        self._config = (config or DiffProcessingConfig()).validate()

    def build_full_file_patch(self, original_file_str: str, new_file_str: str) -> str:
        """Build a single unified-diff hunk that covers the entire file.

        Preserves final-newline state with Git-style
        ``\\ No newline at end of file`` markers when either side lacks a trailing newline.
        """
        orig_lines, orig_nl = _lines_and_final_newline(original_file_str)
        new_lines, new_nl = _lines_and_final_newline(new_file_str)

        orig_count = len(orig_lines)
        new_count = len(new_lines)
        start1 = 0 if orig_count == 0 else 1
        start2 = 0 if new_count == 0 else 1
        header = f"@@ -{start1},{orig_count} +{start2},{new_count} @@"
        body_lines: list[str] = []
        sm = difflib.SequenceMatcher(None, orig_lines, new_lines)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i1, i2):
                    body_lines.append(" " + orig_lines[k])
            elif tag == "delete":
                for k in range(i1, i2):
                    body_lines.append("-" + orig_lines[k])
            elif tag == "insert":
                for k in range(j1, j2):
                    body_lines.append("+" + new_lines[k])
            elif tag == "replace":
                for k in range(i1, i2):
                    body_lines.append("-" + orig_lines[k])
                for k in range(j1, j2):
                    body_lines.append("+" + new_lines[k])

        body_lines = _append_no_newline_markers(
            body_lines,
            orig_ends_with_newline=orig_nl,
            new_ends_with_newline=new_nl,
            orig_count=orig_count,
            new_count=new_count,
        )
        return "\n".join(["", header] + body_lines)

    def build_full_file_patch_chunked(
        self,
        original_file_str: str,
        new_file_str: str,
        chunk_size: int | None = None,
        large_file_threshold: int | None = None,
    ) -> str:
        """Build a unified diff with chunked processing for large files.

        For files larger than large_file_threshold, splits processing into
        chunks to avoid O(N²) complexity issues with difflib.SequenceMatcher.
        """
        chunk_size = chunk_size if chunk_size is not None else self._config.chunk_size
        large_file_threshold = large_file_threshold if large_file_threshold is not None else self._config.large_file_threshold
        max_diff_size = self._config.max_diff_size

        orig_lines, orig_nl = _lines_and_final_newline(original_file_str)
        new_lines, new_nl = _lines_and_final_newline(new_file_str)

        max_lines = max(len(orig_lines), len(new_lines))
        if max_lines > max_diff_size:
            from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason

            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT,
                message=f"File line count {max_lines} exceeds max_diff_size {max_diff_size}",
                observed=max_lines,
                limit=max_diff_size,
            )

        # Small files: single-hunk path preserves EOF markers correctly.
        if max_lines <= large_file_threshold:
            return self.build_full_file_patch(original_file_str, new_file_str)

        self._get_logger().info(f"Using chunked diff processing for large file ({max_lines} lines)")

        hunks: list[str] = []
        chunk_index = 0
        total_chunks = (max_lines + chunk_size - 1) // chunk_size if max_lines else 0

        while chunk_index * chunk_size < max_lines:
            start_line = chunk_index * chunk_size
            end_line = min((chunk_index + 1) * chunk_size, max_lines)
            is_last_chunk = chunk_index + 1 >= total_chunks

            orig_chunk = orig_lines[start_line:end_line]
            new_chunk = new_lines[start_line:end_line]

            hunk = self._build_chunk_hunk(
                orig_chunk,
                new_chunk,
                start_line + 1,
                start_line + 1,
                apply_eof_markers=is_last_chunk,
                orig_ends_with_newline=orig_nl,
                new_ends_with_newline=new_nl,
                file_orig_count=len(orig_lines),
                file_new_count=len(new_lines),
            )
            if hunk:
                hunks.append(hunk)

            chunk_index += 1

        return "\n".join(hunks) if hunks else ""

    def _build_chunk_hunk(
        self,
        orig_lines: list[str],
        new_lines: list[str],
        orig_start: int,
        new_start: int,
        *,
        apply_eof_markers: bool = False,
        orig_ends_with_newline: bool = True,
        new_ends_with_newline: bool = True,
        file_orig_count: int = 0,
        file_new_count: int = 0,
    ) -> str:
        """Build a diff hunk for a chunk of lines.

        When ``apply_eof_markers`` is true (last chunk of a large file), inserts
        Git-style ``\\ No newline at end of file`` markers for the whole file's
        final-newline state — matching ``build_full_file_patch``.
        """
        if not orig_lines and not new_lines:
            return ""

        orig_count = len(orig_lines)
        new_count = len(new_lines)

        header = f"@@ -{orig_start},{orig_count} +{new_start},{new_count} @@"
        body_lines: list[str] = []

        sm = difflib.SequenceMatcher(None, orig_lines, new_lines)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i1, i2):
                    body_lines.append(" " + orig_lines[k])
            elif tag == "delete":
                for k in range(i1, i2):
                    body_lines.append("-" + orig_lines[k])
            elif tag == "insert":
                for k in range(j1, j2):
                    body_lines.append("+" + new_lines[k])
            elif tag == "replace":
                for k in range(i1, i2):
                    body_lines.append("-" + orig_lines[k])
                for k in range(j1, j2):
                    body_lines.append("+" + new_lines[k])

        # Full-context contract: emit equal-only chunks too (mode-only / identical
        # large files must not produce an empty body while the small-file path
        # would include equal context lines).
        if apply_eof_markers:
            body_lines = _append_no_newline_markers(
                body_lines,
                orig_ends_with_newline=orig_ends_with_newline,
                new_ends_with_newline=new_ends_with_newline,
                orig_count=file_orig_count,
                new_count=file_new_count,
            )
        return "\n".join(["", header] + body_lines)
