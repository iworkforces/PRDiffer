from typing import Any, cast

import pytest

from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE
from prdiffer.domain.entities.generated_file_diff import GeneratedFileDiff
from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService


class DummyGitHubAPI:
    def initialize_client(self, github_token=None, timeout=30):
        return None


class OversizedDiffGenerator:
    def generate_ordered_file_diffs(self, file_patches: list[FilePatchInfo]) -> list[GeneratedFileDiff]:
        return [GeneratedFileDiff(index=i, path=p.filename, previous_path=None, diff="+" * 50) for i, p in enumerate(file_patches)]


def test_build_pr_diff_strict_rejects_oversized_response():
    from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason

    service = GitHubPRDiffService(
        github_api_client=cast(Any, DummyGitHubAPI()),
        diff_generator=cast(Any, OversizedDiffGenerator()),
        file_processor=cast(Any, object()),
        logger=None,
    )

    diff_files = [
        FilePatchInfo(
            filename="auth/config.py",
            patch="+ # TODO: add validation",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=10,
            num_minus_lines=2,
        )
    ]

    service._diff_max_total_chars = 10

    with pytest.raises(FullDiffIncompleteError) as exc:
        service._build_pr_diff_strict(diff_files)
    assert exc.value.reason is FullDiffIncompleteReason.RESPONSE_SIZE_LIMIT
