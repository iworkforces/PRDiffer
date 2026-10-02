"""Comprehensive tests for GitHubPRDiffService."""

from typing import Any

import pytest
from unittest.mock import MagicMock, patch
from github import GithubException

from prdiffer.infrastructure.services.pr_diff_service import (
    GitHubPRDiffService,
    PR_SERVICE_EXCEPTIONS,
)
from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot

_BASE_TIP = "a" * 40
_MERGE_BASE = "b" * 40
_HEAD = "c" * 40


@pytest.fixture
def mock_github_api():
    """Create mock GitHub API client."""
    mock = MagicMock()
    mock.initialize_client = MagicMock()
    mock._get_pygithub_repository = MagicMock()
    mock._get_pygithub_pull_request = MagicMock()
    return mock


@pytest.fixture
def mock_file_processor():
    """Create mock file processor."""
    mock = MagicMock()
    mock.process_files_to_patches = MagicMock(return_value=[])
    mock.max_files_allowed = 50
    mock._pattern_matcher.is_valid_file = lambda name: True
    return mock


@pytest.fixture
def mock_diff_generator():
    """Create mock diff generator."""
    mock = MagicMock()
    mock.generate_ordered_file_diffs = MagicMock(return_value=[])
    return mock


@pytest.fixture
def mock_logger():
    """Create mock logger."""
    return MagicMock()


@pytest.fixture
def sample_file_patch():
    """Create sample FilePatchInfo."""
    return FilePatchInfo(
        filename="src/test.py",
        base_file="old content",
        head_file="new content",
        patch="@@ -1,2 +1,2 @@",
        edit_type=EDIT_TYPE.MODIFIED,
        num_plus_lines=5,
        num_minus_lines=3,
    )


class TestGitHubPRDiffServiceInit:
    """Tests for GitHubPRDiffService initialization."""

    def test_init_with_default_api_client(self, mock_diff_generator, mock_file_processor):
        """Test the GitHub API client defaults when not injected."""
        with patch.dict("os.environ", {"GITHUB_TOKEN": "", "GITHUB_TIMEOUT": "30"}):
            service = GitHubPRDiffService(diff_generator=mock_diff_generator, file_processor=mock_file_processor)

            assert service._github_api is not None
            assert service._diff_generator is mock_diff_generator
            assert service._file_processor is mock_file_processor

    def test_init_with_custom_components(self, mock_github_api, mock_diff_generator, mock_file_processor, mock_logger):
        """Test initialization with custom components."""
        service = GitHubPRDiffService(
            github_api_client=mock_github_api,
            diff_generator=mock_diff_generator,
            file_processor=mock_file_processor,
            logger=mock_logger,
        )

        assert service._github_api is mock_github_api
        assert service._diff_generator is mock_diff_generator
        assert service._file_processor is mock_file_processor
        assert service._logger is mock_logger

    def test_init_requires_keyword_collaborators(self, mock_diff_generator, mock_file_processor):
        """Collaborators are keyword-only so positional misbinding fails loudly."""
        service_cls: Any = GitHubPRDiffService
        with pytest.raises(TypeError):
            service_cls(mock_diff_generator, mock_file_processor)


class TestGenerateDiffContent:
    """Tests for _generate_diff_content method."""

    def test_generate_diff_with_file_processor(self, mock_github_api, mock_diff_generator, mock_file_processor, mock_logger):
        """Test diff generation at the snapshot head and merge-base refs."""
        mock_repo = MagicMock()
        mock_pr = MagicMock()

        mock_file = MagicMock()
        mock_file.filename = "test.py"
        mock_pr.get_files.return_value = [mock_file]

        mock_file_processor.process_files_to_patches.return_value = [
            FilePatchInfo(
                filename="test.py",
                base_file="",
                head_file="",
                patch="patch",
                edit_type=EDIT_TYPE.MODIFIED,
                num_plus_lines=1,
                num_minus_lines=1,
            )
        ]

        service = GitHubPRDiffService(
            github_api_client=mock_github_api,
            diff_generator=mock_diff_generator,
            file_processor=mock_file_processor,
            logger=mock_logger,
        )
        snapshot = PRDiffSnapshot("o", "r", 1, _BASE_TIP, _MERGE_BASE, _HEAD, 1)

        result = service._generate_diff_content(mock_repo, mock_pr, snapshot=snapshot)

        assert len(result) == 1
        mock_file_processor.process_files_to_patches.assert_called_once_with([mock_file], mock_repo, _HEAD, _MERGE_BASE)

    def test_generate_diff_no_files(self, mock_github_api, mock_diff_generator, mock_file_processor, mock_logger):
        """Empty file list returns empty patch list (authoritative empty inventory)."""
        mock_repo = MagicMock()
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = []

        service = GitHubPRDiffService(
            github_api_client=mock_github_api,
            diff_generator=mock_diff_generator,
            file_processor=mock_file_processor,
            logger=mock_logger,
        )
        snapshot = PRDiffSnapshot("o", "r", 1, _BASE_TIP, _MERGE_BASE, _HEAD, 0)

        result = service._generate_diff_content(mock_repo, mock_pr, snapshot=snapshot)

        assert result == []
        mock_file_processor.process_files_to_patches.assert_not_called()


class TestPRServiceExceptions:
    """Tests for PR_SERVICE_EXCEPTIONS tuple."""

    def test_exceptions_tuple(self):
        """Test that expected exceptions are in the tuple."""
        assert GithubException in PR_SERVICE_EXCEPTIONS
        assert TimeoutError in PR_SERVICE_EXCEPTIONS
        assert ConnectionError in PR_SERVICE_EXCEPTIONS
        assert OSError in PR_SERVICE_EXCEPTIONS
        assert RuntimeError in PR_SERVICE_EXCEPTIONS
        assert ValueError in PR_SERVICE_EXCEPTIONS
        assert TypeError in PR_SERVICE_EXCEPTIONS


class TestStrictInventoryFailurePropagation:
    """Session/snapshot path must not convert provider inventory failures to empty."""

    def test_page_two_failure_does_not_return_empty(self):
        from unittest.mock import MagicMock
        import pytest
        from github import GithubException
        from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
        from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService

        base = "a" * 40
        mb = "b" * 40
        head = "c" * 40
        snapshot = PRDiffSnapshot("o", "r", 1, base, mb, head, 2)

        class PageTwoFail:
            def __iter__(self):
                yield MagicMock(filename="a.py")
                raise GithubException(500, {"message": "page2"}, None)

        service = GitHubPRDiffService.__new__(GitHubPRDiffService)
        service._logger = MagicMock()
        service._file_processor = MagicMock()
        service._file_processor.max_files_allowed = 50
        service._file_processor._pattern_matcher.is_valid_file = lambda name: True
        service._file_processor.process_files_to_patches = MagicMock(return_value=[])
        service._diff_generator = None

        repo = MagicMock()
        pr = MagicMock()
        pr.get_files.return_value = PageTwoFail()

        with pytest.raises(GithubException):
            service._generate_diff_content(repo, pr, snapshot=snapshot)
        service._file_processor.process_files_to_patches.assert_not_called()

    def test_authoritative_zero_returns_empty_list(self):
        from unittest.mock import MagicMock
        from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
        from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService

        base = "a" * 40
        mb = "b" * 40
        head = "c" * 40
        snapshot = PRDiffSnapshot("o", "r", 1, base, mb, head, 0)
        service = GitHubPRDiffService.__new__(GitHubPRDiffService)
        service._logger = MagicMock()
        service._file_processor = MagicMock()
        service._file_processor.max_files_allowed = 50
        service._file_processor._pattern_matcher.is_valid_file = lambda name: True
        repo = MagicMock()
        pr = MagicMock()
        pr.get_files.return_value = []
        result = service._generate_diff_content(repo, pr, snapshot=snapshot)
        assert result == []
