"""Unit tests for pr_diff_service returning FilePatchInfo list.

Tests that the session content step returns List[FilePatchInfo] for the snapshot refs.
"""

from unittest.mock import Mock

from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService

_BASE_TIP = "a" * 40
_MERGE_BASE = "b" * 40
_HEAD = "c" * 40


class TestGenerateDiffContentReturnsFilePatchList:
    """Test that _generate_diff_content returns FilePatchInfo list."""

    def test_generate_diff_content_returns_file_patch_list(self):
        """Test _generate_diff_content returns List[FilePatchInfo] built at snapshot refs."""
        # Arrange
        mock_github_api_client = Mock()
        mock_file_processor = Mock()
        mock_diff_generator = Mock()

        file_patch_1 = FilePatchInfo(
            filename="file1.ts",
            edit_type=EDIT_TYPE.ADDED,
            num_plus_lines=100,
            num_minus_lines=0,
            patch="@@ -0,0 +1,100 @@\n+new\n",
        )

        file_patch_2 = FilePatchInfo(
            filename="file2.ts",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=50,
            num_minus_lines=25,
            patch="@@ -1,3 +1,8 @@\n-old\n+new\n",
        )

        mock_file_processor.process_files_to_patches.return_value = [
            file_patch_1,
            file_patch_2,
        ]
        mock_file_processor.max_files_allowed = 50
        mock_file_processor._pattern_matcher.is_valid_file.return_value = True

        service = GitHubPRDiffService(
            github_api_client=mock_github_api_client,
            file_processor=mock_file_processor,
            diff_generator=mock_diff_generator,
        )

        mock_repository = Mock()
        mock_pull_request = Mock()
        provider_files = [Mock(filename="file1.ts"), Mock(filename="file2.ts")]
        mock_pull_request.get_files.return_value = provider_files
        snapshot = PRDiffSnapshot("o", "r", 1, _BASE_TIP, _MERGE_BASE, _HEAD, 2)

        # Act
        diff_files = service._generate_diff_content(mock_repository, mock_pull_request, snapshot=snapshot)

        # Assert
        assert isinstance(diff_files, list)
        assert len(diff_files) == 2
        assert all(isinstance(f, FilePatchInfo) for f in diff_files)
        assert diff_files[0].filename == "file1.ts"
        assert diff_files[1].filename == "file2.ts"
        mock_file_processor.process_files_to_patches.assert_called_once_with(provider_files, mock_repository, _HEAD, _MERGE_BASE)
