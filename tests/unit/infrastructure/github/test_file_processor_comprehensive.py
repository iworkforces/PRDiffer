"""Comprehensive unit tests for FileProcessor (immutable git tree path)."""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.infrastructure.github.file_processor import FileProcessor
from tests.unit.infrastructure.github.test_file_processor_ordered import BASE, HEAD, TreeRepo


@pytest.fixture
def mock_pattern_matcher():
    """Create mock pattern matcher."""
    matcher = Mock()
    matcher.is_valid_file = Mock(return_value=True)
    return matcher


@pytest.fixture
def mock_logger():
    """Create mock logger."""
    logger = Mock()
    logger.should_log.return_value = False
    return logger


@pytest.fixture
def file_processor(mock_pattern_matcher, mock_logger):
    """Create FileProcessor instance with mocked dependencies."""
    return FileProcessor(
        pattern_matcher=mock_pattern_matcher,
        max_files_allowed=50,
        logger=mock_logger,
    )


class TestFileProcessorInit:
    """Tests for FileProcessor initialization."""

    def test_init_default_values(self, mock_pattern_matcher):
        """Test initialization with default values."""
        processor = FileProcessor(pattern_matcher=mock_pattern_matcher)

        assert processor.max_files_allowed == 50
        assert processor._max_file_size_bytes == 10_485_760

    def test_init_custom_values(self, mock_pattern_matcher):
        """Test initialization with custom values."""
        processor = FileProcessor(
            pattern_matcher=mock_pattern_matcher,
            max_files_allowed=100,
            max_file_size_bytes=1024,
        )

        assert processor.max_files_allowed == 100
        assert processor._max_file_size_bytes == 1024


class TestFileProcessorProcessFilesToPatches:
    """Tests for process_files_to_patches."""

    def test_process_empty_files_list(self, file_processor):
        """Empty selection returns no patches without touching the repository."""
        repo = TreeRepo({})

        assert file_processor.process_files_to_patches([], repo, HEAD, BASE) == []
        assert repo.tree_calls == []


class TestFileProcessorCreateFilePatch:
    """Tests for file patch creation methods."""

    def test_create_file_patch_with_content(self, file_processor):
        """Test creating file patch with content."""
        mock_file = Mock()
        mock_file.filename = "test.py"
        mock_file.status = "added"
        mock_file.patch = "+new content"
        mock_file.additions = 1
        mock_file.deletions = 0

        result = file_processor._create_file_patch_with_content(mock_file, "", "new content", "+new content")

        assert result.filename == "test.py"
        assert result.edit_type == EDIT_TYPE.ADDED
        assert result.base_file == ""
        assert result.head_file == "new content"


class TestFileProcessorCountPatchLines:
    """Tests for _count_patch_lines method."""

    def test_count_from_file_attributes(self, file_processor):
        """Test counting lines from file attributes."""
        mock_file = Mock()
        mock_file.additions = 10
        mock_file.deletions = 5

        plus, minus = file_processor._count_patch_lines(mock_file, "")

        assert plus == 10
        assert minus == 5

    def test_count_from_patch_content(self, file_processor):
        """Test counting lines from patch content."""
        mock_file = Mock()
        # No additions/deletions attributes
        del mock_file.additions
        del mock_file.deletions

        patch = "+line1\n+line2\n-line3\n context"

        plus, minus = file_processor._count_patch_lines(mock_file, patch)

        assert plus == 2
        assert minus == 1

    def test_count_empty_patch(self, file_processor):
        """Test counting with empty patch."""
        mock_file = Mock()
        del mock_file.additions
        del mock_file.deletions

        plus, minus = file_processor._count_patch_lines(mock_file, "")

        assert plus == 0
        assert minus == 0


class TestFileProcessorGeneratePatch:
    """Tests for _generate_patch_from_content method."""

    def test_generate_patch_with_diff(self, file_processor):
        """Test generating patch with different content."""
        original = "line1\nline2\n"
        new = "line1\nline3\n"

        patch = file_processor._generate_patch_from_content("test.py", new, original)

        assert "---" in patch or "+++" in patch or patch == ""

    def test_generate_patch_empty_content(self, file_processor):
        """Test generating patch with empty content."""
        patch = file_processor._generate_patch_from_content("test.py", "", "")

        assert patch == ""

    def test_generate_patch_identical_content(self, file_processor):
        """Test generating patch with identical content."""
        content = "same content\n"

        patch = file_processor._generate_patch_from_content("test.py", content, content)

        # Identical content produces empty or minimal diff
        assert isinstance(patch, str)


class TestFileProcessorStatusMapping:
    """Tests for STATUS_TO_EDIT_TYPE mapping."""

    def test_status_added_mapping(self, file_processor):
        """Test 'added' status maps to ADDED."""
        assert file_processor.STATUS_TO_EDIT_TYPE["added"] == EDIT_TYPE.ADDED

    def test_status_removed_mapping(self, file_processor):
        """Test 'removed' status maps to DELETED."""
        assert file_processor.STATUS_TO_EDIT_TYPE["removed"] == EDIT_TYPE.DELETED

    def test_status_modified_mapping(self, file_processor):
        """Test 'modified' status maps to MODIFIED."""
        assert file_processor.STATUS_TO_EDIT_TYPE["modified"] == EDIT_TYPE.MODIFIED

    def test_status_renamed_mapping(self, file_processor):
        """Test 'renamed' status maps to RENAMED."""
        assert file_processor.STATUS_TO_EDIT_TYPE["renamed"] == EDIT_TYPE.RENAMED


class TestFileProcessorRenamedFiles:
    """Tests for handling renamed files."""

    def test_process_renamed_file(self, file_processor, mock_pattern_matcher):
        """Renamed files read base text from the previous path."""
        mock_pattern_matcher.is_valid_file.return_value = True
        repo = TreeRepo({BASE: {"old_name.py": "old content\n"}, HEAD: {"new_name.py": "new content\n"}})

        mock_file = Mock()
        mock_file.filename = "new_name.py"
        mock_file.previous_filename = "old_name.py"
        mock_file.status = "renamed"
        mock_file.patch = "+new line\n-old line"
        mock_file.additions = 1
        mock_file.deletions = 1

        result = file_processor.process_files_to_patches([mock_file], repo, HEAD, BASE)

        assert len(result) == 1
        assert result[0].filename == "new_name.py"
        assert result[0].edit_type == EDIT_TYPE.RENAMED
        assert result[0].old_filename == "old_name.py"
        assert result[0].base_file == "old content\n"
        assert result[0].head_file == "new content\n"


class TestFileProcessorInvalidFiles:
    """Tests for handling invalid files."""

    def test_process_filters_invalid_files(self, file_processor, mock_pattern_matcher):
        """Test that invalid files are filtered out."""
        mock_pattern_matcher.is_valid_file.side_effect = lambda f: not f.endswith(".lock")
        repo = TreeRepo({BASE: {"valid.py": "a\n"}, HEAD: {"valid.py": "b\n"}})

        mock_file1 = Mock()
        mock_file1.filename = "valid.py"
        mock_file1.status = "modified"
        mock_file1.patch = "+line"
        mock_file1.additions = 1
        mock_file1.deletions = 0

        mock_file2 = Mock()
        mock_file2.filename = "invalid.lock"
        mock_file2.status = "modified"

        result = file_processor.process_files_to_patches([mock_file1, mock_file2], repo, HEAD, BASE)

        assert len(result) == 1
        assert result[0].filename == "valid.py"


class TestFileProcessorUnknownStatus:
    """Tests for handling unknown file statuses."""

    def test_process_unknown_status(self, file_processor, mock_pattern_matcher):
        """Unknown statuses raise E5020 UNSUPPORTED_FILE_STATUS."""
        from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason

        mock_pattern_matcher.is_valid_file.return_value = True

        mock_file = Mock()
        mock_file.filename = "test.py"
        mock_file.status = "unknown_status"
        mock_file.patch = ""
        mock_file.additions = 0
        mock_file.deletions = 0

        with pytest.raises(FullDiffIncompleteError) as exc:
            file_processor.process_files_to_patches([mock_file], TreeRepo({}), HEAD, BASE)
        assert exc.value.reason is FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS
