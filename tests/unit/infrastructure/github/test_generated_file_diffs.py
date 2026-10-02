"""Strict ordered full-context generation tests."""

from __future__ import annotations

import pytest

from prdiffer.domain.entities.file_patch import EDIT_TYPE, FilePatchInfo
from prdiffer.domain.entities.generated_file_diff import GeneratedFileDiff
from prdiffer.domain.exceptions import DiffGenerationError, FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.utils.diff_utils import DiffUtils


def _patch(
    *,
    name: str,
    edit: EDIT_TYPE,
    base: str = "",
    head: str = "",
    patch: str = "",
    old: str | None = None,
    old_mode: str | None = None,
    new_mode: str | None = None,
) -> FilePatchInfo:
    return FilePatchInfo(
        filename=name,
        base_file=base,
        head_file=head,
        patch=patch,
        edit_type=edit,
        old_filename=old,
        old_mode=old_mode,
        new_mode=new_mode,
        num_plus_lines=1 if head else 0,
        num_minus_lines=1 if base else 0,
    )


@pytest.mark.unit
class TestGenerateOrderedFileDiffs:
    def test_all_edit_statuses_in_provider_order(self) -> None:
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        files = [
            _patch(name="a.py", edit=EDIT_TYPE.MODIFIED, base="line1\n", head="line1\nchanged\n"),
            _patch(name="b.py", edit=EDIT_TYPE.DELETED, base="gone\n", head=""),
            _patch(name="c_new.py", edit=EDIT_TYPE.RENAMED, base="same\n", head="same\n", old="c_old.py"),
            _patch(name="d.py", edit=EDIT_TYPE.ADDED, base="", head="new\n"),
        ]
        results = generator.generate_ordered_file_diffs(files)
        assert [r.index for r in results] == [0, 1, 2, 3]
        assert [r.path for r in results] == ["a.py", "b.py", "c_new.py", "d.py"]
        assert results[2].previous_path == "c_old.py"
        assert "rename from c_old.py" in results[2].diff
        assert "rename to c_new.py" in results[2].diff
        # Full context includes surrounding lines, not only a hunk-only provider patch.
        assert "line1" in results[0].diff
        assert "changed" in results[0].diff

    def test_missing_provider_patch_recovered_from_text(self) -> None:
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        files = [
            _patch(
                name="x.py",
                edit=EDIT_TYPE.MODIFIED,
                base="alpha\nbeta\ngamma\n",
                head="alpha\nBETA\ngamma\n",
                patch="",  # missing provider patch
            )
        ]
        results = generator.generate_ordered_file_diffs(files)
        assert len(results) == 1
        assert "alpha" in results[0].diff
        assert "gamma" in results[0].diff
        assert "BETA" in results[0].diff

    def test_provider_hunk_differs_from_full_context(self) -> None:
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        hunk_only = "@@ -2,1 +2,1 @@\n-beta\n+BETA\n"
        full_base = "alpha\nbeta\ngamma\n"
        full_head = "alpha\nBETA\ngamma\n"
        files = [_patch(name="x.py", edit=EDIT_TYPE.MODIFIED, base=full_base, head=full_head, patch=hunk_only)]
        results = generator.generate_ordered_file_diffs(files)
        assert results[0].diff != hunk_only
        assert "alpha" in results[0].diff
        assert "gamma" in results[0].diff

    def test_zero_byte_add_and_delete(self) -> None:
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        files = [
            _patch(name="empty_add.py", edit=EDIT_TYPE.ADDED, base="", head=""),
            _patch(name="empty_del.py", edit=EDIT_TYPE.DELETED, base="", head=""),
        ]
        results = generator.generate_ordered_file_diffs(files)
        assert len(results) == 2
        assert all(isinstance(r, GeneratedFileDiff) for r in results)

    def test_mode_change_headers_exact(self) -> None:
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        files = [
            _patch(
                name="script.sh",
                edit=EDIT_TYPE.MODIFIED,
                base="echo hi\n",
                head="echo hi\n",
                old_mode="100644",
                new_mode="100755",
            )
        ]
        results = generator.generate_ordered_file_diffs(files)
        assert results[0].diff.startswith("old mode 100644\nnew mode 100755\n")
        assert "old mode 100644" in results[0].diff
        assert "new mode 100755" in results[0].diff

    def test_equal_or_absent_modes_emit_no_mode_header(self) -> None:
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        files = [
            _patch(name="a.py", edit=EDIT_TYPE.MODIFIED, base="x\n", head="y\n"),
            _patch(
                name="b.py",
                edit=EDIT_TYPE.MODIFIED,
                base="x\n",
                head="y\n",
                old_mode="100644",
                new_mode="100644",
            ),
        ]
        results = generator.generate_ordered_file_diffs(files)
        assert "old mode" not in results[0].diff
        assert "new mode" not in results[0].diff
        assert "old mode" not in results[1].diff

    def test_rename_plus_mode_header_order_deterministic(self) -> None:
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        files = [
            _patch(
                name="new.sh",
                edit=EDIT_TYPE.RENAMED,
                base="same\n",
                head="same\n",
                old="old.sh",
                old_mode="100644",
                new_mode="100755",
            )
        ]
        results = generator.generate_ordered_file_diffs(files)
        diff = results[0].diff
        mode_pos = diff.index("old mode 100644")
        rename_pos = diff.index("rename from old.sh")
        assert mode_pos < rename_pos
        assert "rename to new.sh" in diff
        assert "new mode 100755" in diff

    def test_unknown_status_raises_e5020(self) -> None:
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        files = [_patch(name="u.py", edit=EDIT_TYPE.UNKNOWN, base="a", head="b")]
        with pytest.raises(FullDiffIncompleteError) as exc:
            generator.generate_ordered_file_diffs(files)
        assert exc.value.reason is FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS

    def test_unexpected_builder_exception_is_e5003(self) -> None:
        class BoomUtils(DiffUtils):
            def build_full_file_patch(self, original_file_str: str, new_file_str: str) -> str:
                raise RuntimeError("algorithm exploded")

            def build_full_file_patch_chunked(self, original_file_str: str, new_file_str: str, **kwargs) -> str:
                raise RuntimeError("algorithm exploded")

        generator = DiffGenerator(diff_utils=BoomUtils(), parallel_enabled=False)
        files = [_patch(name="x.py", edit=EDIT_TYPE.MODIFIED, base="a\n", head="b\n")]
        with pytest.raises(DiffGenerationError) as exc:
            generator.generate_ordered_file_diffs(files)
        assert exc.value.error_code.code == "E5003"

    def test_source_mentioning_diff_truncated_is_not_false_positive(self) -> None:
        """Real file content may include the truncation notice string (settings/tests)."""
        utils = DiffUtils()
        generator = DiffGenerator(diff_utils=utils, parallel_enabled=False)
        base = 'notice = "legacy"\n'
        head = 'diff.truncation_notice = "[DIFF TRUNCATED]"\nother = 1\n'
        files = [_patch(name="settings.toml", edit=EDIT_TYPE.MODIFIED, base=base, head=head)]
        results = generator.generate_ordered_file_diffs(files)
        assert len(results) == 1
        assert "[DIFF TRUNCATED]" in results[0].diff
        assert "truncation_notice" in results[0].diff
