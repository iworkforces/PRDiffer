"""Unit tests for PRDiff entity.

Tests PRDiff dataclass which represents pull request information
with structured files array (breaking change - files removed).
"""

import json
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff


def _file_response(path: str = "test.ts") -> FileDiffResponse:
    return FileDiffResponse(
        path=path,
        status=EDIT_TYPE.ADDED,
        stats=FileStats(additions=10, deletions=5),
        diff="diff",
    )


class TestPRDiffCreation:
    """Test suite for PRDiff creation and validation."""

    def test_head_sha_is_required(self):
        with pytest.raises(TypeError, match="head_sha"):
            PRDiff(**{})

    @pytest.mark.parametrize("head_sha", ["", None, 123, False, ()])
    def test_invalid_head_sha_is_rejected(self, head_sha):
        with pytest.raises(ValueError, match="nonempty string"):
            PRDiff(head_sha=head_sha)

    def test_pr_diff_creation_empty_files(self):
        """Test creating a PRDiff with empty files list."""
        pr_diff = PRDiff(files=(), head_sha="c" * 40)

        assert pr_diff.files == ()

    def test_pr_diff_creation_with_files_array(self):
        """Test creating PRDiff with files array structure."""
        files = (
            FileDiffResponse(
                path="src/file1.ts",
                status=EDIT_TYPE.ADDED,
                stats=FileStats(additions=100, deletions=0),
                diff="@@ -0,0 +1,100 @@\n+new content\n",
            ),
            FileDiffResponse(
                path="src/file2.ts",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=50, deletions=25),
                diff="@@ -1,3 +1,8 @@\n-old\n+new\n",
            ),
        )
        pr_diff = PRDiff(files=files, head_sha="c" * 40)

        assert len(pr_diff.files) == 2
        assert pr_diff.files[0].path == "src/file1.ts"
        assert pr_diff.files[1].path == "src/file2.ts"


class TestPRDiffProperties:
    """Test suite for PRDiff property methods."""

    def test_has_files_true(self):
        """Test PRDiff has files when files array is not empty."""
        pr_diff = PRDiff(
            head_sha="c" * 40,
            files=(
                FileDiffResponse(
                    path="test.py",
                    status=EDIT_TYPE.MODIFIED,
                    stats=FileStats(additions=1, deletions=1),
                    diff="diff",
                ),
            ),
        )

        assert len(pr_diff.files) == 1

    def test_has_files_false_empty(self):
        """Test PRDiff has no files when files array is empty."""
        pr_diff = PRDiff(files=(), head_sha="c" * 40)

        assert len(pr_diff.files) == 0


class TestPRDiffEdgeCases:
    """Test edge cases and boundary conditions for PRDiff."""

    def test_files_with_various_statuses(self):
        """Test PRDiff with various edit status values."""
        files = (
            FileDiffResponse(
                path="added.py",
                status=EDIT_TYPE.ADDED,
                stats=FileStats(additions=10, deletions=0),
                diff="diff",
            ),
            FileDiffResponse(
                path="modified.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=5, deletions=3),
                diff="diff",
            ),
            FileDiffResponse(
                path="deleted.py",
                status=EDIT_TYPE.DELETED,
                stats=FileStats(additions=0, deletions=8),
                diff="diff",
            ),
            FileDiffResponse(
                path="renamed.py",
                status=EDIT_TYPE.RENAMED,
                stats=FileStats(additions=0, deletions=0),
                diff="diff",
            ),
        )
        pr_diff = PRDiff(files=files, head_sha="c" * 40)

        assert len(pr_diff.files) == 4
        assert pr_diff.files[0].status == EDIT_TYPE.ADDED
        assert pr_diff.files[1].status == EDIT_TYPE.MODIFIED
        assert pr_diff.files[2].status == EDIT_TYPE.DELETED
        assert pr_diff.files[3].status == EDIT_TYPE.RENAMED


class TestPRDiffSerialization:
    """Test serialization capabilities of PRDiff."""

    def test_asdict(self):
        pr_diff = PRDiff(
            head_sha="c" * 40,
            files=(
                FileDiffResponse(
                    path="test.ts",
                    status=EDIT_TYPE.MODIFIED,
                    stats=FileStats(additions=10, deletions=5),
                    diff="@@ -1,1 +1,1 @@\n-old\n+new",
                ),
            ),
        )

        data = asdict(pr_diff)

        assert "files" in data
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "test.ts"

    def test_json_serialization(self):
        pr_diff = PRDiff(
            head_sha="c" * 40,
            files=(
                FileDiffResponse(
                    path="test.py",
                    status=EDIT_TYPE.ADDED,
                    stats=FileStats(additions=100, deletions=0),
                    diff="@@ -0,0 +1,100 @@\n+content",
                ),
            ),
        )

        json_string = json.dumps(asdict(pr_diff))

        assert '"files"' in json_string
        assert '"path": "test.py"' in json_string
        assert '"status": "added"' in json_string

    def test_construct_from_dict_data(self):
        data = {
            "files": [
                {
                    "path": "src/component.ts",
                    "status": "deleted",
                    "stats": {"additions": 0, "deletions": 50},
                    "diff": "@@ -1,50 +1,0 @@\n-removed lines",
                }
            ]
        }

        files = tuple(
            FileDiffResponse(
                path=file_data["path"],
                status=EDIT_TYPE(file_data["status"]),
                stats=FileStats(**file_data["stats"]),
                diff=file_data["diff"],
            )
            for file_data in data["files"]
        )
        pr_diff = PRDiff(files=files, head_sha="c" * 40)

        assert len(pr_diff.files) == 1
        assert pr_diff.files[0].path == "src/component.ts"

    def test_round_trip_serialization(self):
        """Test serialization and deserialization round trip."""
        original = PRDiff(
            head_sha="c" * 40,
            files=(
                FileDiffResponse(
                    path="test.ts",
                    status=EDIT_TYPE.ADDED,
                    stats=FileStats(additions=75, deletions=10),
                    diff="test diff",
                ),
            ),
        )

        json_data = json.dumps(asdict(original))
        payload = json.loads(json_data)

        restored = PRDiff(
            head_sha=payload["head_sha"],
            files=tuple(
                FileDiffResponse(
                    path=file_data["path"],
                    status=EDIT_TYPE(file_data["status"]),
                    stats=FileStats(**file_data["stats"]),
                    diff=file_data["diff"],
                )
                for file_data in payload["files"]
            ),
        )

        assert len(restored.files) == len(original.files)
        assert restored.files[0].path == original.files[0].path


class TestPRDiffImmutability:
    """Test immutability characteristics of PRDiff."""

    def test_immutability_with_frozen(self):
        """Test that PRDiff instances are immutable (frozen=True)."""
        pr_diff = PRDiff(files=(_file_response(),), head_sha="c" * 40)

        with pytest.raises(FrozenInstanceError):
            setattr(pr_diff, "files", ())

    def test_replace_creates_new_instance(self):
        """Test dataclasses.replace creates updated copy."""
        original = PRDiff(files=(_file_response(),), head_sha="c" * 40)
        updated_files = (_file_response(path="modified.ts"),)

        copy = replace(original, files=updated_files)

        assert original.files[0].path == "test.ts"
        assert copy.files[0].path == "modified.ts"
