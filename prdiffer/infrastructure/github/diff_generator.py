"""Full-context diff generation service."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

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


class DiffGenerator:
    """Service for generating ordered full-context per-file diffs."""

    def __init__(
        self,
        diff_utils: DiffServiceInterface,
        parallel_enabled: bool = False,
        parallel_threshold: int = 3,
        max_workers: int = 4,
        logger: logging.Logger | None = None,
    ) -> None:
        self._diff_utils = diff_utils
        # Parallel generation is enabled explicitly by factory/settings (not bare ctor).
        self._parallel_enabled = bool(parallel_enabled)
        self._parallel_threshold = parallel_threshold
        self._max_workers = max(1, int(max_workers))
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
            body = self._build_full_context_body(base_text, head_text)
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

    def _build_full_context_body(self, base_text: str, head_text: str) -> str:
        """Build full-file unified body from base/head text only (never provider hunks)."""
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
        return body


def get_diff_generator(
    diff_utils: DiffServiceInterface,
    parallel_enabled: bool = False,
    parallel_threshold: int = 3,
    max_workers: int = 4,
) -> DiffGenerator:
    """Get a configured diff generator instance."""
    return DiffGenerator(
        diff_utils=diff_utils,
        parallel_enabled=parallel_enabled,
        parallel_threshold=parallel_threshold,
        max_workers=max_workers,
    )
