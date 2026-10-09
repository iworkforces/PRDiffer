from unittest.mock import MagicMock

import pytest

from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService
from prdiffer.infrastructure.utils.diff_utils import DiffUtils


@pytest.mark.parametrize("file_count,line_size", [(1, 650_000), (2, 350_000)], ids=["single", "aggregate"])
def test_build_pr_diff_strict_preserves_large_full_output(file_count: int, line_size: int) -> None:
    # Given real content and the production full-context generator.
    content = "x" * line_size + "\n"
    patches = [FilePatchInfo(filename=f"large{i}.py", base_file="", head_file=content, patch="", edit_type=EDIT_TYPE.ADDED) for i in range(file_count)]
    service = GitHubPRDiffService(
        github_api_client=MagicMock(),
        diff_generator=DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False),
        file_processor=MagicMock(),
    )

    # When the service assembles the generated files.
    result = service._build_pr_diff_strict(patches, head_sha="c" * 40)

    # Then every generated character and ordered file survives unchanged.
    expected = ["\n@@ -0,0 +1,1 @@\n+" + "x" * line_size for _ in range(file_count)]
    assert [file.path for file in result.files] == [f"large{i}.py" for i in range(file_count)]
    assert [file.diff for file in result.files] == expected
    assert sum(map(len, expected)) > 600_000
    if file_count > 1:
        assert all(len(diff) < 600_000 for diff in expected)
