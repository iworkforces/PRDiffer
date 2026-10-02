"""Comprehensive tests for DiffGenerator."""

import pytest
from unittest.mock import MagicMock

from prdiffer.infrastructure.github.diff_generator import (
    DiffGenerator,
    get_diff_generator,
)
from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE


@pytest.fixture
def mock_diff_utils():
    """Create mock diff utils."""
    mock = MagicMock()
    mock.build_full_file_patch_chunked.return_value = "\n@@ -1 +1 @@\n-old\n+new"
    return mock


@pytest.fixture
def sample_file_patch():
    """Create sample FilePatchInfo."""
    return FilePatchInfo(
        filename="src/test.py",
        base_file="old content\nline1\nline2\n",
        head_file="new content\nline1\nline2 modified\nline3\n",
        patch="@@ -1,3 +1,4 @@\n-old content\n+new content\n line1\n line2\n+line3\n",
        edit_type=EDIT_TYPE.MODIFIED,
        num_plus_lines=2,
        num_minus_lines=1,
    )


@pytest.fixture
def sample_file_patches(sample_file_patch):
    """Create list of sample FilePatchInfo objects."""
    return [
        sample_file_patch,
        FilePatchInfo(
            filename="src/new_file.py",
            base_file="",
            head_file="new file content\n",
            patch="@@ -0,0 +1 @@\n+new file content\n",
            edit_type=EDIT_TYPE.ADDED,
            num_plus_lines=1,
            num_minus_lines=0,
        ),
    ]


def _modified_files(count: int) -> list[FilePatchInfo]:
    return [
        FilePatchInfo(
            filename=f"file{i}.py",
            base_file="old",
            head_file="new",
            patch=f"patch{i}",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=1,
            num_minus_lines=1,
        )
        for i in range(count)
    ]


class TestDiffGeneratorInit:
    """Tests for DiffGenerator initialization."""

    def test_init_with_defaults(self, mock_diff_utils):
        """Test initialization with default parameters."""
        generator = DiffGenerator(diff_utils=mock_diff_utils)

        assert generator._diff_utils is mock_diff_utils
        # Bare ctor stays sequential; factory/settings opt into parallel generation.
        assert generator._parallel_enabled is False
        assert generator._parallel_threshold == 3
        assert generator._max_workers == 4

    def test_init_parallel_enabled(self, mock_diff_utils):
        """Test explicit parallel enablement and worker bound."""
        generator = DiffGenerator(diff_utils=mock_diff_utils, parallel_enabled=True, max_workers=2)

        assert generator._parallel_enabled is True
        assert generator._max_workers == 2

    def test_init_max_workers_floor_is_one(self, mock_diff_utils):
        """Non-positive worker counts are clamped to one."""
        generator = DiffGenerator(diff_utils=mock_diff_utils, max_workers=0)

        assert generator._max_workers == 1

    def test_init_custom_threshold(self, mock_diff_utils):
        """Test custom parallel threshold."""
        generator = DiffGenerator(
            diff_utils=mock_diff_utils,
            parallel_threshold=5,
        )

        assert generator._parallel_threshold == 5

    def test_init_custom_logger(self, mock_diff_utils):
        """Test initialization with custom logger."""
        mock_logger = MagicMock()
        generator = DiffGenerator(
            diff_utils=mock_diff_utils,
            logger=mock_logger,
        )

        assert generator._logger is mock_logger


class TestGenerateOrderedFileDiffs:
    """Tests for generate_ordered_file_diffs."""

    def test_empty_file_list(self, mock_diff_utils):
        """Test with empty file list."""
        generator = DiffGenerator(diff_utils=mock_diff_utils)

        assert generator.generate_ordered_file_diffs([]) == []

    def test_sequential_builds_full_context(self, mock_diff_utils, sample_file_patch):
        """Strict path builds full context from base/head (not provider hunk alone)."""
        generator = DiffGenerator(diff_utils=mock_diff_utils)
        result = generator.generate_ordered_file_diffs([sample_file_patch])

        assert len(result) == 1
        assert result[0].path == "src/test.py"
        mock_diff_utils.build_full_file_patch_chunked.assert_called_once_with(sample_file_patch.base_file, sample_file_patch.head_file)

    def test_parallel_disabled_preserves_order(self, mock_diff_utils, sample_file_patches):
        """Disabled parallelism still yields one ordered result per file."""
        generator = DiffGenerator(diff_utils=mock_diff_utils, parallel_enabled=False)
        result = generator.generate_ordered_file_diffs(sample_file_patches)

        assert [item.index for item in result] == [0, 1]
        assert [item.path for item in result] == ["src/test.py", "src/new_file.py"]

    def test_parallel_flag_still_returns_one_result_per_file(self, mock_diff_utils):
        """Even with parallel enabled, strict ordered generation returns N results."""
        generator = DiffGenerator(
            diff_utils=mock_diff_utils,
            parallel_enabled=True,
            parallel_threshold=3,
        )
        result = generator.generate_ordered_file_diffs(_modified_files(5))

        assert [item.index for item in result] == [0, 1, 2, 3, 4]
        assert [item.path for item in result] == [f"file{i}.py" for i in range(5)]

    def test_file_without_patch_recovered_from_content(self, mock_diff_utils):
        """Missing provider patch is recovered from complete base/head text."""
        file_no_patch = FilePatchInfo(
            filename="empty.py",
            base_file="old\n",
            head_file="new\n",
            patch="",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=1,
            num_minus_lines=1,
        )

        generator = DiffGenerator(diff_utils=mock_diff_utils)
        result = generator.generate_ordered_file_diffs([file_no_patch])

        assert len(result) == 1
        assert result[0].diff


class TestGetDiffGenerator:
    """Tests for get_diff_generator factory function."""

    def test_get_diff_generator_defaults(self, mock_diff_utils):
        """Test factory with default parameters."""
        generator = get_diff_generator(diff_utils=mock_diff_utils)

        assert generator._diff_utils is mock_diff_utils
        assert generator._parallel_threshold == 3

    def test_get_diff_generator_custom_params(self, mock_diff_utils):
        """Test factory with custom parameters."""
        generator = get_diff_generator(
            diff_utils=mock_diff_utils,
            parallel_enabled=False,
            parallel_threshold=5,
            max_workers=2,
        )

        assert generator._parallel_enabled is False
        assert generator._parallel_threshold == 5
        assert generator._max_workers == 2


class TestSequentialVsParallel:
    """Tests comparing sequential vs parallel processing."""

    def test_below_threshold_is_sequential(self, mock_diff_utils, monkeypatch):
        """Files below threshold never use the parallel path."""
        generator = DiffGenerator(diff_utils=mock_diff_utils, parallel_enabled=True, parallel_threshold=3)
        parallel = MagicMock(side_effect=AssertionError("parallel path must not run below threshold"))
        monkeypatch.setattr(generator, "_generate_ordered_file_diffs_parallel", parallel)

        result = generator.generate_ordered_file_diffs(_modified_files(1))

        assert len(result) == 1
        parallel.assert_not_called()

    def test_at_threshold_returns_ordered_results(self, mock_diff_utils):
        """Strict ordered generation returns one result per file regardless of parallel flags."""
        generator = DiffGenerator(
            diff_utils=mock_diff_utils,
            parallel_enabled=True,
            parallel_threshold=3,
        )

        result = generator.generate_ordered_file_diffs(_modified_files(3))
        assert [item.index for item in result] == [0, 1, 2]
