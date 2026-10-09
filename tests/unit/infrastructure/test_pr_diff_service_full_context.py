"""Service-level full-context PRDiff construction tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from prdiffer.domain.entities.file_patch import EDIT_TYPE, FilePatchInfo
from prdiffer.domain.entities.generated_file_diff import GeneratedFileDiff
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService


@pytest.mark.unit
def test_build_pr_diff_maps_generated_full_context() -> None:
    generator = MagicMock()
    generator.generate_ordered_file_diffs.return_value = [
        GeneratedFileDiff(
            index=0,
            path="a.py",
            previous_path=None,
            diff="@@ full\n alpha\n-beta\n+BETA\n gamma\n",
        )
    ]
    service = GitHubPRDiffService(
        github_api_client=MagicMock(),
        diff_generator=generator,
        file_processor=MagicMock(),
        logger=MagicMock(),
    )
    patches = [
        FilePatchInfo(
            filename="a.py",
            base_file="alpha\nbeta\ngamma\n",
            head_file="alpha\nBETA\ngamma\n",
            patch="@@ -2 +2 @@\n-beta\n+BETA\n",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=1,
            num_minus_lines=1,
        )
    ]
    pr_diff = service._build_pr_diff_strict(patches)
    assert len(pr_diff.files) == 1
    assert "alpha" in pr_diff.files[0].diff
    assert "gamma" in pr_diff.files[0].diff
    assert pr_diff.files[0].diff != patches[0].patch


@pytest.mark.unit
def test_build_pr_diff_identity_mismatch_is_e5020() -> None:
    generator = MagicMock()
    generator.generate_ordered_file_diffs.return_value = [GeneratedFileDiff(index=0, path="wrong.py", previous_path=None, diff="+x")]
    service = GitHubPRDiffService(
        github_api_client=MagicMock(),
        diff_generator=generator,
        file_processor=MagicMock(),
        logger=MagicMock(),
    )
    patches = [
        FilePatchInfo(filename="a.py", edit_type=EDIT_TYPE.MODIFIED, patch="+x", num_plus_lines=1),
    ]
    with pytest.raises(FullDiffIncompleteError) as exc:
        service._build_pr_diff_strict(patches)
    assert exc.value.reason is FullDiffIncompleteReason.DIFF_GENERATION_FAILED
