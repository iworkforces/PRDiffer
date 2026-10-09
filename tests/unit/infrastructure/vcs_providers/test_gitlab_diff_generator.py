"""Tests for GitLab ordered full-context response assembly."""

from __future__ import annotations

import pytest

from prdiffer.domain.entities.file_content import FileContentAvailable
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.utils.diff_utils import DiffUtils
from prdiffer.infrastructure.vcs_providers.gitlab_content import GitLabFileContents
from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import GitLabDiffAssembler
from prdiffer.infrastructure.vcs_providers.gitlab_inventory import GitLabInventoryFile
from prdiffer.infrastructure.vcs_providers.gitlab_models import GitLabDiffRecord


def _item(index: int, edit: EDIT_TYPE, old: str, new: str, **modes: str | None) -> GitLabInventoryFile:
    return GitLabInventoryFile(
        record=GitLabDiffRecord(
            old_path=old,
            new_path=new,
            new_file=edit is EDIT_TYPE.ADDED,
            deleted_file=edit is EDIT_TYPE.DELETED,
            renamed_file=edit is EDIT_TYPE.RENAMED,
            a_mode=modes.get("a_mode"),
            b_mode=modes.get("b_mode"),
            diff=None,
        ),
        edit_type=edit,
        index=index,
    )


def _content(
    index: int,
    path: str,
    edit: EDIT_TYPE,
    base: str,
    head: str,
    *,
    previous: str | None = None,
    old_mode: str | None = None,
    new_mode: str | None = None,
) -> GitLabFileContents:
    return GitLabFileContents(
        index=index,
        path=path,
        previous_path=previous,
        edit_type=edit,
        base=FileContentAvailable(base),
        head=FileContentAvailable(head),
        old_mode=old_mode,
        new_mode=new_mode,
    )


@pytest.mark.unit
class TestGitLabDiffAssembler:
    def test_empty_diff_preserves_snapshot_head(self) -> None:
        assembler = GitLabDiffAssembler(DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False))
        result = assembler.assemble((), (), head_sha="f" * 40)
        assert result.files == ()
        assert result.head_sha == "f" * 40

    def test_mixed_status_ordered_full_context(self) -> None:
        gen = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
        assembler = GitLabDiffAssembler(gen)
        inv = (
            _item(0, EDIT_TYPE.MODIFIED, "a.py", "a.py"),
            _item(1, EDIT_TYPE.ADDED, "b.py", "b.py"),
            _item(2, EDIT_TYPE.DELETED, "c.py", "c.py"),
            _item(3, EDIT_TYPE.RENAMED, "old.py", "new.py"),
            _item(4, EDIT_TYPE.MODIFIED, "mode.sh", "mode.sh", a_mode="100644", b_mode="100755"),
            _item(5, EDIT_TYPE.ADDED, "empty.py", "empty.py"),
        )
        contents = (
            _content(0, "a.py", EDIT_TYPE.MODIFIED, "line1\n", "line1\nchanged\n"),
            _content(1, "b.py", EDIT_TYPE.ADDED, "", "new\n"),
            _content(2, "c.py", EDIT_TYPE.DELETED, "gone\n", ""),
            _content(3, "new.py", EDIT_TYPE.RENAMED, "same\n", "same\n", previous="old.py"),
            _content(4, "mode.sh", EDIT_TYPE.MODIFIED, "echo\n", "echo\n", old_mode="100644", new_mode="100755"),
            _content(5, "empty.py", EDIT_TYPE.ADDED, "", ""),
        )
        pr_diff = assembler.assemble(inv, contents, head_sha="f" * 40)
        assert pr_diff.head_sha == "f" * 40
        assert [f.path for f in pr_diff.files] == ["a.py", "b.py", "c.py", "new.py", "mode.sh", "empty.py"]
        assert pr_diff.files[3].previous_path == "old.py"
        assert "rename from old.py" in pr_diff.files[3].diff
        assert pr_diff.files[4].diff.startswith("old mode 100644\nnew mode 100755\n")
        assert "line1" in pr_diff.files[0].diff
        assert "changed" in pr_diff.files[0].diff
        # No provider hunk passthrough markers required; stats from generated text
        assert pr_diff.files[0].stats.additions >= 1

    def test_equal_noop_modified_fails_hard(self) -> None:
        """Equal content + equal/missing modes must always E5020, even if gen emits text."""
        gen = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
        assembler = GitLabDiffAssembler(gen)
        inv = (_item(0, EDIT_TYPE.MODIFIED, "a.py", "a.py", a_mode="100644", b_mode="100644"),)
        contents = (
            _content(
                0,
                "a.py",
                EDIT_TYPE.MODIFIED,
                "identical body\n",
                "identical body\n",
                old_mode="100644",
                new_mode="100644",
            ),
        )
        with pytest.raises(FullDiffIncompleteError) as exc:
            assembler.assemble(inv, contents, head_sha="f" * 40)
        assert exc.value.reason is FullDiffIncompleteReason.DIFF_GENERATION_FAILED
        assert exc.value.details.get("path") == "a.py"

    def test_equal_content_mode_change_allowed(self) -> None:
        gen = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
        assembler = GitLabDiffAssembler(gen)
        inv = (_item(0, EDIT_TYPE.MODIFIED, "mode.sh", "mode.sh", a_mode="100644", b_mode="100755"),)
        contents = (
            _content(
                0,
                "mode.sh",
                EDIT_TYPE.MODIFIED,
                "echo\n",
                "echo\n",
                old_mode="100644",
                new_mode="100755",
            ),
        )
        pr_diff = assembler.assemble(inv, contents, head_sha="f" * 40)
        assert pr_diff.files[0].diff.startswith("old mode 100644\nnew mode 100755\n")

    @pytest.mark.parametrize("file_count,line_size", [(1, 650_000), (2, 350_000)], ids=["single", "aggregate"])
    def test_large_full_output_preserved(self, file_count: int, line_size: int) -> None:
        # Given real contents and the production full-context generator.
        gen = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
        assembler = GitLabDiffAssembler(gen)
        text = "x" * line_size + "\n"
        inv = tuple(_item(i, EDIT_TYPE.ADDED, f"large{i}.py", f"large{i}.py") for i in range(file_count))
        contents = tuple(_content(i, f"large{i}.py", EDIT_TYPE.ADDED, "", text) for i in range(file_count))

        # When the assembler generates the response.
        result = assembler.assemble(inv, contents, head_sha="f" * 40)

        # Then all files retain their exact full output, even in aggregate.
        expected = ["\n@@ -0,0 +1,1 @@\n+" + "x" * line_size for _ in range(file_count)]
        assert [file.path for file in result.files] == [f"large{i}.py" for i in range(file_count)]
        assert [file.diff for file in result.files] == expected
        assert sum(map(len, expected)) > 600_000
        if file_count > 1:
            assert all(len(diff) < 600_000 for diff in expected)

    def test_index_mismatch_fails(self) -> None:
        gen = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
        assembler = GitLabDiffAssembler(gen)
        inv = (_item(0, EDIT_TYPE.ADDED, "a.py", "a.py"),)
        contents = (_content(1, "a.py", EDIT_TYPE.ADDED, "", "x\n"),)
        with pytest.raises(FullDiffIncompleteError) as exc:
            assembler.assemble(inv, contents, head_sha="f" * 40)
        assert exc.value.reason is FullDiffIncompleteReason.DIFF_GENERATION_FAILED


class TestCountUnifiedStatsHunkState:
    def test_double_plus_minus_source_lines_count_inside_hunk(self):
        from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import _count_unified_stats

        diff = "\n".join(
            [
                "--- a/f",
                "+++ b/f",
                "@@ -1,1 +1,1 @@",
                "---old",
                "+++new",
            ]
        )
        additions, deletions = _count_unified_stats(diff)
        assert deletions == 1
        assert additions == 1

    def test_file_headers_outside_hunk_do_not_count(self):
        from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import _count_unified_stats

        diff = "\n".join(
            [
                "--- a/path",
                "+++ b/path",
                "@@ -1,1 +1,1 @@",
                "-x",
                "+y",
            ]
        )
        additions, deletions = _count_unified_stats(diff)
        assert additions == 1
        assert deletions == 1

    def test_mode_and_rename_metadata_ignored(self):
        from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import _count_unified_stats

        diff = "\n".join(
            [
                "old mode 100644",
                "new mode 100755",
                "rename from a",
                "rename to b",
                "@@ -0,0 +0,0 @@",
            ]
        )
        assert _count_unified_stats(diff) == (0, 0)

    def test_no_newline_marker_ignored(self):
        from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import _count_unified_stats

        diff = "\n".join(
            [
                "@@ -1,1 +1,1 @@",
                "-a",
                "\\ No newline at end of file",
                "+b",
                "\\ No newline at end of file",
            ]
        )
        assert _count_unified_stats(diff) == (1, 1)
