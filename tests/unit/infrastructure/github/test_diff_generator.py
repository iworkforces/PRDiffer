"""Unit tests for DiffGenerator mode and end-of-file headers."""


class TestModeAndEofHeaders:
    def test_added_file_emits_new_file_mode(self):
        from unittest.mock import MagicMock
        from prdiffer.domain.entities.file_patch import EDIT_TYPE, FilePatchInfo
        from prdiffer.infrastructure.github.diff_generator import DiffGenerator

        mock_diff_utils = MagicMock()
        mock_diff_utils.build_full_file_patch_chunked.return_value = "\n@@ -0,0 +1,1 @@\n+content\n"
        gen = DiffGenerator(diff_utils=mock_diff_utils)
        patch = FilePatchInfo(
            filename="a.py",
            base_file="",
            head_file="content\n",
            patch="",
            edit_type=EDIT_TYPE.ADDED,
            new_mode="100644",
        )
        result = gen.generate_ordered_file_diffs([patch])
        assert result[0].diff.startswith("new file mode 100644\n")

    def test_deleted_file_emits_deleted_file_mode(self):
        from unittest.mock import MagicMock
        from prdiffer.domain.entities.file_patch import EDIT_TYPE, FilePatchInfo
        from prdiffer.infrastructure.github.diff_generator import DiffGenerator

        mock_diff_utils = MagicMock()
        mock_diff_utils.build_full_file_patch_chunked.return_value = "\n@@ -1,1 +0,0 @@\n-content\n"
        gen = DiffGenerator(diff_utils=mock_diff_utils)
        patch = FilePatchInfo(
            filename="a.py",
            base_file="content\n",
            head_file="",
            patch="",
            edit_type=EDIT_TYPE.DELETED,
            old_mode="100755",
        )
        result = gen.generate_ordered_file_diffs([patch])
        assert result[0].diff.startswith("deleted file mode 100755\n")

    def test_gitlink_add_emits_new_file_mode_160000(self):
        from unittest.mock import MagicMock
        from prdiffer.domain.entities.file_patch import EDIT_TYPE, FilePatchInfo
        from prdiffer.infrastructure.github.diff_generator import DiffGenerator

        mock_diff_utils = MagicMock()
        mock_diff_utils.build_full_file_patch_chunked.return_value = "\n@@ -0,0 +1,1 @@\n+Subproject commit abc\n"
        gen = DiffGenerator(diff_utils=mock_diff_utils)
        patch = FilePatchInfo(
            filename="sub",
            base_file="",
            head_file="Subproject commit abc\n",
            patch="",
            edit_type=EDIT_TYPE.ADDED,
            new_mode="160000",
        )
        result = gen.generate_ordered_file_diffs([patch])
        assert result[0].diff.startswith("new file mode 160000\n")

    def test_renderer_exception_never_returns_provider_hunk(self):
        from unittest.mock import MagicMock
        import pytest
        from prdiffer.domain.entities.file_patch import EDIT_TYPE, FilePatchInfo
        from prdiffer.domain.exceptions import DiffGenerationError
        from prdiffer.infrastructure.github.diff_generator import DiffGenerator

        mock_diff_utils = MagicMock()
        mock_diff_utils.build_full_file_patch_chunked.side_effect = RuntimeError("boom")
        gen = DiffGenerator(diff_utils=mock_diff_utils)
        patch = FilePatchInfo(
            filename="a.py",
            base_file="a\n",
            head_file="b\n",
            patch="@@ provider hunk only @@\n",
            edit_type=EDIT_TYPE.MODIFIED,
        )
        with pytest.raises(DiffGenerationError):
            gen.generate_ordered_file_diffs([patch])
