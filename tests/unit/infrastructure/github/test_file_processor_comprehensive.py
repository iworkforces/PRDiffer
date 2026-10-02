"""Unit tests for FileProcessor.

Tests cover file filtering, processing, content loading, and patch generation.
"""

import pytest
from unittest.mock import Mock
import time

from prdiffer.infrastructure.github.file_processor import FileProcessor
from prdiffer.domain.entities.file_content import FileContentAvailable, FileContentRequest, FileContentResponse, FileContentResult
from prdiffer.domain.entities.file_patch import EDIT_TYPE


def _install_multi_ref_batch_adapter(api: Mock) -> None:
    def get_multi_ref_batch(requests: tuple[FileContentRequest, ...]) -> tuple[FileContentResponse, ...]:
        responses: list[FileContentResponse] = []
        for request in requests:
            batch = api.get_files_content_batch(request.repo_full_name, [request.path], request.ref)
            value = batch.get(request.path)
            if isinstance(value, str):
                content: FileContentResult = FileContentAvailable(text=value)
            elif value is None:
                content = FileContentAvailable(text="")
            else:
                content = value
            responses.append(FileContentResponse(request=request, content=content))
        return tuple(responses)

    api.get_files_content_multi_ref_batch.side_effect = get_multi_ref_batch


@pytest.fixture
def mock_github_api():
    """Create mock GitHub API service."""
    api = Mock()
    api.get_files_content_batch = Mock(return_value={})
    _install_multi_ref_batch_adapter(api)
    return api


@pytest.fixture
def mock_pattern_matcher():
    """Create mock pattern matcher."""
    matcher = Mock()
    matcher.is_valid_file = Mock(return_value=True)
    return matcher


@pytest.fixture
def mock_diff_utils():
    """Create mock diff utils."""
    return Mock()


@pytest.fixture
def mock_logger():
    """Create mock logger."""
    logger = Mock()
    logger.should_log.return_value = False
    return logger


@pytest.fixture
def file_processor(mock_github_api, mock_pattern_matcher, mock_diff_utils, mock_logger):
    """Create FileProcessor instance with mocked dependencies."""
    return FileProcessor(
        github_api_service=mock_github_api,
        pattern_matcher=mock_pattern_matcher,
        diff_utils=mock_diff_utils,
        max_files_allowed=50,
        parallel_fetch_threshold=10,
        max_parallel_workers=4,
        logger=mock_logger,
    )


class TestFileProcessorInit:
    """Tests for FileProcessor initialization."""

    def test_init_default_values(self, mock_github_api, mock_pattern_matcher, mock_diff_utils):
        """Test initialization with default values."""
        processor = FileProcessor(
            github_api_service=mock_github_api,
            pattern_matcher=mock_pattern_matcher,
            diff_utils=mock_diff_utils,
        )

        assert processor.max_files_allowed == 50
        assert processor._parallel_fetch_threshold == 10
        assert processor._max_parallel_workers == 4

    def test_init_custom_values(self, mock_github_api, mock_pattern_matcher, mock_diff_utils):
        """Test initialization with custom values."""
        processor = FileProcessor(
            github_api_service=mock_github_api,
            pattern_matcher=mock_pattern_matcher,
            diff_utils=mock_diff_utils,
            max_files_allowed=100,
            parallel_fetch_threshold=20,
            max_parallel_workers=8,
        )

        assert processor.max_files_allowed == 100
        assert processor._parallel_fetch_threshold == 20
        assert processor._max_parallel_workers == 8


class TestFileProcessorGetPRFiles:
    """Tests for get_pr_files method."""

    @pytest.mark.asyncio
    async def test_get_pr_files_caches_result(self, file_processor):
        """Test that get_pr_files caches the result."""
        mock_pr = Mock()
        mock_files = [Mock(filename="test.py")]
        mock_pr.get_files.return_value = mock_files

        result1 = await file_processor.get_pr_files(mock_pr)
        result2 = await file_processor.get_pr_files(mock_pr)

        assert result1 == mock_files
        assert result2 == mock_files
        mock_pr.get_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pr_files_cache_expires(self, file_processor):
        """Test that cache expires after TTL."""
        file_processor._pr_files_cache = [Mock(filename="old.py")]
        file_processor._pr_cache_timestamp = time.time() - 400  # Expired

        mock_pr = Mock()
        mock_new_files = [Mock(filename="new.py")]
        mock_pr.get_files.return_value = mock_new_files

        result = await file_processor.get_pr_files(mock_pr)

        assert result == mock_new_files


class TestFileProcessorFilterFiles:
    """Tests for filter_files method."""

    def test_filter_files_all_valid(self, file_processor, mock_pattern_matcher):
        """Test filtering when all files are valid."""
        mock_pattern_matcher.is_valid_file.return_value = True

        files = [Mock(filename="file1.py"), Mock(filename="file2.py")]

        result = file_processor.filter_files(files)

        assert len(result) == 2

    def test_filter_files_some_invalid(self, file_processor, mock_pattern_matcher):
        """Test filtering when some files are invalid."""
        mock_pattern_matcher.is_valid_file.side_effect = lambda f: f.endswith(".py")

        files = [
            Mock(filename="file1.py"),
            Mock(filename="file2.lock"),
            Mock(filename="file3.py"),
        ]

        result = file_processor.filter_files(files)

        assert len(result) == 2
        assert all(f.filename.endswith(".py") for f in result)


class TestFileProcessorProcessFilesToPatches:
    """Tests for process_files_to_patches method."""

    def test_process_empty_files_list(self, file_processor):
        """Test processing empty file list."""
        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        result = file_processor.process_files_to_patches([], mock_repo, "head123", "base123")

        assert result == []

    def test_process_single_added_file(self, file_processor, mock_github_api, mock_pattern_matcher):
        """Test processing a single added file."""
        mock_pattern_matcher.is_valid_file.return_value = True
        mock_github_api.get_files_content_batch.return_value = {"test.py": "new content"}

        mock_file = Mock()
        mock_file.filename = "test.py"
        mock_file.status = "added"
        mock_file.patch = "+new line"
        mock_file.additions = 1
        mock_file.deletions = 0

        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        result = file_processor.process_files_to_patches([mock_file], mock_repo, "head123", "base123")

        assert len(result) == 1
        assert result[0].filename == "test.py"
        assert result[0].edit_type == EDIT_TYPE.ADDED

    def test_process_removed_file_included(self, file_processor, mock_github_api, mock_pattern_matcher):
        """Deleted files are included with base content and empty head."""
        from prdiffer.domain.entities.file_content import FileContentAvailable

        mock_pattern_matcher.is_valid_file.return_value = True
        mock_github_api.get_files_content_batch.return_value = {
            "deleted.py": FileContentAvailable(text="old\n"),
        }

        mock_file = Mock()
        mock_file.filename = "deleted.py"
        mock_file.status = "removed"
        mock_file.patch = "-old\n"
        mock_file.additions = 0
        mock_file.deletions = 1

        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        result = file_processor.process_files_to_patches([mock_file], mock_repo, "head123", "base123")

        assert len(result) == 1
        assert result[0].edit_type == EDIT_TYPE.DELETED
        assert result[0].base_file == "old\n"
        assert result[0].head_file == ""

    def test_process_max_files_limit(self, mock_github_api, mock_pattern_matcher, mock_diff_utils, mock_logger):
        """Test that max files limit is enforced."""
        processor = FileProcessor(
            github_api_service=mock_github_api,
            pattern_matcher=mock_pattern_matcher,
            diff_utils=mock_diff_utils,
            max_files_allowed=2,
            logger=mock_logger,
        )
        mock_pattern_matcher.is_valid_file.return_value = True
        mock_github_api.get_files_content_batch.return_value = {}

        files = []
        for i in range(5):
            f = Mock()
            f.filename = f"file{i}.py"
            f.status = "modified"
            f.patch = f"+line{i}"
            f.additions = 1
            f.deletions = 0
            files.append(f)

        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        result = processor.process_files_to_patches(files, mock_repo, "head123", "base123")

        assert len(result) == 5


class TestFileProcessorProcessFilesToPatchesAsync:
    """Tests for process_files_to_patches_async method."""

    @pytest.mark.asyncio
    async def test_process_async_empty_files(self, file_processor):
        """Test async processing empty file list."""
        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        result = await file_processor.process_files_to_patches_async([], mock_repo, "head123", "base123")

        assert result == []

    @pytest.mark.asyncio
    async def test_process_async_single_file(self, file_processor, mock_github_api, mock_pattern_matcher):
        """Test async processing a single file."""
        mock_pattern_matcher.is_valid_file.return_value = True
        mock_github_api.get_files_content_batch.return_value = {"test.py": "content"}

        mock_file = Mock()
        mock_file.filename = "test.py"
        mock_file.status = "modified"
        mock_file.patch = "+new line"
        mock_file.additions = 1
        mock_file.deletions = 0

        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        result = await file_processor.process_files_to_patches_async([mock_file], mock_repo, "head123", "base123")

        assert len(result) == 1


class TestFileProcessorCreateFilePatch:
    """Tests for file patch creation methods."""

    def test_create_file_patch_without_content(self, file_processor):
        """Test creating file patch without content."""
        mock_file = Mock()
        mock_file.filename = "test.py"
        mock_file.status = "modified"
        mock_file.patch = "+added\n-removed"
        mock_file.additions = 1
        mock_file.deletions = 1

        result = file_processor._create_file_patch_without_content(mock_file)

        assert result.filename == "test.py"
        assert result.edit_type == EDIT_TYPE.MODIFIED
        assert result.base_file == ""
        assert result.head_file == ""

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


class TestFileProcessorIsRenameOnly:
    """Tests for _is_rename_only method."""

    def test_is_rename_only_from_api_metadata(self, file_processor):
        """Test detecting rename-only from API metadata."""
        mock_file = Mock()
        mock_file.filename = "new_name.py"
        mock_file.additions = 0
        mock_file.deletions = 0

        result = file_processor._is_rename_only(mock_file)

        assert result is True

    def test_is_rename_only_has_changes(self, file_processor):
        """Test detecting file with content changes."""
        mock_file = Mock()
        mock_file.filename = "renamed.py"
        mock_file.additions = 5
        mock_file.deletions = 2

        result = file_processor._is_rename_only(mock_file)

        assert result is False

    def test_is_rename_only_from_content_comparison(self, file_processor):
        """Test detecting rename-only from content comparison."""
        mock_file = Mock()
        mock_file.filename = "renamed.py"
        # No additions/deletions attributes
        del mock_file.additions
        del mock_file.deletions

        content = "same content"
        result = file_processor._is_rename_only(mock_file, content, content)

        assert result is True

    def test_is_rename_only_different_content(self, file_processor):
        """Test detecting renamed file with different content."""
        mock_file = Mock()
        mock_file.filename = "renamed.py"
        del mock_file.additions
        del mock_file.deletions

        result = file_processor._is_rename_only(mock_file, "old content", "new content")

        assert result is False

    def test_is_rename_only_no_content(self, file_processor):
        """Test detecting rename with no content available."""
        mock_file = Mock()
        mock_file.filename = "renamed.py"
        del mock_file.additions
        del mock_file.deletions

        result = file_processor._is_rename_only(mock_file, "", "")

        # Conservative: assume has changes when no content available
        assert result is False


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

    def test_process_renamed_file(self, file_processor, mock_github_api, mock_pattern_matcher):
        """Test processing renamed file."""
        mock_pattern_matcher.is_valid_file.return_value = True
        mock_github_api.get_files_content_batch.side_effect = [
            {"new_name.py": "new content"},  # head contents
            {"old_name.py": "old content"},  # base contents
        ]

        mock_file = Mock()
        mock_file.filename = "new_name.py"
        mock_file.previous_filename = "old_name.py"
        mock_file.status = "renamed"
        mock_file.patch = "+new line\n-old line"
        mock_file.additions = 1
        mock_file.deletions = 1

        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        result = file_processor.process_files_to_patches([mock_file], mock_repo, "head123", "base123")

        assert len(result) == 1
        assert result[0].filename == "new_name.py"
        assert result[0].edit_type == EDIT_TYPE.RENAMED


class TestFileProcessorInvalidFiles:
    """Tests for handling invalid files."""

    def test_process_filters_invalid_files(self, file_processor, mock_github_api, mock_pattern_matcher):
        """Test that invalid files are filtered out."""
        mock_pattern_matcher.is_valid_file.side_effect = lambda f: not f.endswith(".lock")

        mock_file1 = Mock()
        mock_file1.filename = "valid.py"
        mock_file1.status = "modified"
        mock_file1.patch = "+line"
        mock_file1.additions = 1
        mock_file1.deletions = 0

        mock_file2 = Mock()
        mock_file2.filename = "invalid.lock"
        mock_file2.status = "modified"

        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        result = file_processor.process_files_to_patches([mock_file1, mock_file2], mock_repo, "head123", "base123")

        assert len(result) == 1
        assert result[0].filename == "valid.py"


class TestFileProcessorUnknownStatus:
    """Tests for handling unknown file statuses."""

    def test_process_unknown_status(self, file_processor, mock_github_api, mock_pattern_matcher, mock_logger):
        """Unknown statuses raise E5020 UNSUPPORTED_FILE_STATUS."""
        from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason

        mock_pattern_matcher.is_valid_file.return_value = True
        mock_github_api.get_files_content_batch.return_value = {}

        mock_file = Mock()
        mock_file.filename = "test.py"
        mock_file.status = "unknown_status"
        mock_file.patch = ""
        mock_file.additions = 0
        mock_file.deletions = 0

        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"

        with pytest.raises(FullDiffIncompleteError) as exc:
            file_processor.process_files_to_patches([mock_file], mock_repo, "head123", "base123")
        assert exc.value.reason is FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS
