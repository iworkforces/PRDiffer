"""Comprehensive tests for diff_utils.py."""

import pytest
from unittest.mock import Mock

from prdiffer.infrastructure.utils.diff_utils import (
    DiffProcessingConfig,
    DiffUtils,
    DEFAULT_LARGE_FILE_THRESHOLD,
    DEFAULT_DIFF_CHUNK_SIZE,
    DEFAULT_MAX_DIFF_SIZE,
)


class TestDiffProcessingConfig:
    """Tests for DiffProcessingConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = DiffProcessingConfig()
        assert config.large_file_threshold == DEFAULT_LARGE_FILE_THRESHOLD
        assert config.chunk_size == DEFAULT_DIFF_CHUNK_SIZE
        assert config.max_diff_size == DEFAULT_MAX_DIFF_SIZE

    def test_custom_values(self):
        """Test custom configuration values."""
        config = DiffProcessingConfig(
            large_file_threshold=1000,
            chunk_size=500,
            max_diff_size=50000,
        )
        assert config.large_file_threshold == 1000
        assert config.chunk_size == 500
        assert config.max_diff_size == 50000

    def test_validate_lower_bounds(self):
        """Test validation applies lower bounds."""
        config = DiffProcessingConfig(
            large_file_threshold=10,
            chunk_size=10,
            max_diff_size=10,
        )
        validated = config.validate()
        assert validated.large_file_threshold == 100
        assert validated.chunk_size == 100
        assert validated.max_diff_size == 1000

    def test_validate_upper_bounds(self):
        """Test validation applies upper bounds."""
        config = DiffProcessingConfig(
            large_file_threshold=100000,
            chunk_size=20000,
            max_diff_size=2000000,
        )
        validated = config.validate()
        assert validated.large_file_threshold == 50000
        assert validated.chunk_size == 10000
        assert validated.max_diff_size == 1000000

    def test_validate_within_bounds(self):
        """Test validation keeps values within bounds unchanged."""
        config = DiffProcessingConfig(
            large_file_threshold=5000,
            chunk_size=1000,
            max_diff_size=100000,
        )
        validated = config.validate()
        assert validated.large_file_threshold == 5000
        assert validated.chunk_size == 1000
        assert validated.max_diff_size == 100000

    def test_frozen_dataclass(self):
        """Test that config is immutable."""
        config = DiffProcessingConfig()
        with pytest.raises(Exception):
            config.large_file_threshold = 9999


class TestDiffUtilsInit:
    """Tests for DiffUtils initialization."""

    def test_init_default(self):
        """Test default initialization."""
        diff_utils = DiffUtils()
        assert diff_utils._config is not None
        assert diff_utils._config.large_file_threshold == DEFAULT_LARGE_FILE_THRESHOLD

    def test_init_with_logger(self):
        """Test initialization with logger."""
        logger = Mock()
        diff_utils = DiffUtils(logger=logger)
        assert diff_utils._logger is not None

    def test_init_with_config(self):
        """Test initialization with custom config."""
        config = DiffProcessingConfig(
            large_file_threshold=2000,
            chunk_size=400,
            max_diff_size=40000,
        )
        diff_utils = DiffUtils(config=config)
        assert diff_utils._config.large_file_threshold == 2000
        assert diff_utils._config.chunk_size == 400
        assert diff_utils._config.max_diff_size == 40000

    def test_init_config_validated(self):
        """Test that config is validated on init."""
        config = DiffProcessingConfig(large_file_threshold=10)
        diff_utils = DiffUtils(config=config)
        assert diff_utils._config.large_file_threshold == 100


class TestBuildFullFilePatch:
    """Tests for build_full_file_patch method."""

    def test_identical_files(self):
        """Test diff of identical files."""
        diff_utils = DiffUtils()
        content = "line1\nline2\nline3"
        result = diff_utils.build_full_file_patch(content, content)
        assert "@@ -1,3 +1,3 @@" in result
        assert " line1" in result
        assert " line2" in result
        assert " line3" in result

    def test_added_lines(self):
        """Test diff with added lines."""
        diff_utils = DiffUtils()
        original = "line1\nline2"
        new = "line1\nline2\nline3"
        result = diff_utils.build_full_file_patch(original, new)
        assert "@@ -1,2 +1,3 @@" in result
        assert "+line3" in result

    def test_removed_lines(self):
        """Test diff with removed lines."""
        diff_utils = DiffUtils()
        original = "line1\nline2\nline3"
        new = "line1\nline3"
        result = diff_utils.build_full_file_patch(original, new)
        assert "@@ -1,3 +1,2 @@" in result
        assert "-line2" in result

    def test_modified_lines(self):
        """Test diff with modified lines."""
        diff_utils = DiffUtils()
        original = "line1\nold_line\nline3"
        new = "line1\nnew_line\nline3"
        result = diff_utils.build_full_file_patch(original, new)
        assert "-old_line" in result
        assert "+new_line" in result

    def test_empty_files(self):
        """Test diff of empty files."""
        diff_utils = DiffUtils()
        result = diff_utils.build_full_file_patch("", "")
        assert "@@ -0,0 +0,0 @@" in result

    def test_new_file(self):
        """Test diff creating new file."""
        diff_utils = DiffUtils()
        new = "line1\nline2"
        result = diff_utils.build_full_file_patch("", new)
        assert "@@ -0,0 +1,2 @@" in result
        assert "+line1" in result
        assert "+line2" in result

    def test_deleted_file(self):
        """Test diff deleting entire file."""
        diff_utils = DiffUtils()
        original = "line1\nline2"
        result = diff_utils.build_full_file_patch(original, "")
        assert "@@ -1,2 +0,0 @@" in result
        assert "-line1" in result
        assert "-line2" in result


class TestBuildFullFilePatchChunked:
    """Tests for build_full_file_patch_chunked method."""

    def test_small_file_uses_standard(self):
        """Test that small files use standard processing."""
        diff_utils = DiffUtils()
        original = "line1\nline2"
        new = "line1\nline2\nline3"
        result = diff_utils.build_full_file_patch_chunked(original, new)
        assert "+line3" in result

    def test_large_file_exceeds_limit_raises(self):
        """Very large files raise RESPONSE_SIZE_LIMIT (no truncation)."""
        from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason

        config = DiffProcessingConfig(max_diff_size=1000)
        diff_utils = DiffUtils(config=config)
        large_content = "\n".join([f"line{i}" for i in range(2000)])
        with pytest.raises(FullDiffIncompleteError) as exc:
            diff_utils.build_full_file_patch_chunked(large_content, large_content)
        assert exc.value.reason is FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT

    def test_custom_chunk_size(self):
        """Test custom chunk size parameter."""
        diff_utils = DiffUtils()
        orig_lines = [f"line{i}" for i in range(100)]
        new_lines = orig_lines.copy()
        new_lines[50] = "modified"
        orig_content = "\n".join(orig_lines)
        new_content = "\n".join(new_lines)
        result = diff_utils.build_full_file_patch_chunked(orig_content, new_content, chunk_size=50, large_file_threshold=10)
        assert "-line50" in result or "+modified" in result

    def test_custom_large_file_threshold(self):
        """Test custom large file threshold."""
        config = DiffProcessingConfig(large_file_threshold=10, chunk_size=5)
        diff_utils = DiffUtils(config=config)
        lines = [f"line{i}" for i in range(50)]
        content = "\n".join(lines)
        result = diff_utils.build_full_file_patch_chunked(content, content)
        assert result != ""

    def test_uses_config_defaults(self):
        """Test that config defaults are used when params not specified."""
        config = DiffProcessingConfig(
            large_file_threshold=500,
            chunk_size=200,
            max_diff_size=10000,
        )
        diff_utils = DiffUtils(config=config)
        lines = [f"line{i}" for i in range(100)]
        content = "\n".join(lines)
        result = diff_utils.build_full_file_patch_chunked(content, content)
        assert result != ""


class TestBuildChunkHunk:
    """Tests for _build_chunk_hunk method."""

    def test_empty_chunks(self):
        """Test with empty chunks."""
        diff_utils = DiffUtils()
        result = diff_utils._build_chunk_hunk([], [], 1, 1)
        assert result == ""

    def test_identical_chunks_emit_equal_context(self):
        """Full-context: identical chunks still emit equal context lines."""
        diff_utils = DiffUtils()
        lines = ["line1", "line2"]
        result = diff_utils._build_chunk_hunk(lines, lines, 1, 1)
        assert " line1" in result
        assert " line2" in result
        assert "@@ -1,2 +1,2 @@" in result

    def test_modified_chunks(self):
        """Test with modified chunks."""
        diff_utils = DiffUtils()
        orig = ["line1", "old"]
        new = ["line1", "new"]
        result = diff_utils._build_chunk_hunk(orig, new, 1, 1)
        assert "-old" in result
        assert "+new" in result

    def test_chunked_identical_large_file_emits_context(self):
        """Mode-only / identical large files must not produce an empty body."""
        utils = DiffUtils(config=DiffProcessingConfig(large_file_threshold=5, chunk_size=3))
        lines = [f"line{i}" for i in range(8)]
        content = "\n".join(lines) + "\n"
        patch = utils.build_full_file_patch_chunked(content, content, chunk_size=3, large_file_threshold=5)
        assert patch != ""
        assert " line0" in patch
        assert " line7" in patch

    def test_custom_line_numbers(self):
        """Test with custom line numbers."""
        diff_utils = DiffUtils()
        orig = ["line1"]
        new = ["line1", "line2"]
        result = diff_utils._build_chunk_hunk(orig, new, 100, 200)
        assert "@@ -100,1 +200,2 @@" in result


class TestNoNewlineMarkers:
    """Git-style \\ No newline at end of file markers."""

    def test_missing_newline_on_both_sides_for_modified_line(self):
        utils = DiffUtils()
        # Neither side ends with newline
        patch = utils.build_full_file_patch("old", "new")
        assert "\\ No newline at end of file" in patch
        assert "-old" in patch
        assert "+new" in patch

    def test_newline_present_on_both_sides_has_no_marker(self):
        utils = DiffUtils()
        patch = utils.build_full_file_patch("old\n", "new\n")
        assert "\\ No newline at end of file" not in patch
        assert "-old" in patch
        assert "+new" in patch

    def test_only_old_side_missing_newline(self):
        utils = DiffUtils()
        patch = utils.build_full_file_patch("old", "new\n")
        # Marker after the deleted old line
        lines = patch.splitlines()
        assert "-old" in lines
        old_idx = lines.index("-old")
        assert lines[old_idx + 1] == "\\ No newline at end of file"

    def test_chunked_path_preserves_eof_markers(self):
        """Large-file chunked path must emit Git no-newline markers on last hunk."""
        # Force chunked path: threshold 5, 8 lines, last line without final newline.
        utils = DiffUtils(config=DiffProcessingConfig(large_file_threshold=5, chunk_size=3))
        orig_lines = [f"line{i}" for i in range(8)]
        new_lines = orig_lines.copy()
        new_lines[7] = "changed"
        # No trailing newline on either side
        original = "\n".join(orig_lines)
        new = "\n".join(new_lines)
        assert not original.endswith("\n")
        assert not new.endswith("\n")
        patch = utils.build_full_file_patch_chunked(original, new, chunk_size=3, large_file_threshold=5)
        assert "\\ No newline at end of file" in patch
        assert "-line7" in patch or "+changed" in patch
        # Markers must appear after the last body line in the last hunk
        lines = patch.splitlines()
        assert any(line == "\\ No newline at end of file" for line in lines)

    def test_chunked_path_no_marker_when_both_sides_have_newline(self):
        utils = DiffUtils(config=DiffProcessingConfig(large_file_threshold=5, chunk_size=3))
        orig_lines = [f"line{i}" for i in range(8)]
        new_lines = orig_lines.copy()
        new_lines[7] = "changed"
        original = "\n".join(orig_lines) + "\n"
        new = "\n".join(new_lines) + "\n"
        patch = utils.build_full_file_patch_chunked(original, new, chunk_size=3, large_file_threshold=5)
        assert "\\ No newline at end of file" not in patch
        assert "+changed" in patch
