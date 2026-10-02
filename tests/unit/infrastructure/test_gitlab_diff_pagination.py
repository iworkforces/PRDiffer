"""Pagination and ordering tests for immutable GitLab MR diff version selection.

Strict full-diff uses one pinned diff version; it never calls the current `/diffs` endpoint.
"""

from __future__ import annotations

import pytest

from prdiffer.domain.exceptions import FullDiffIncompleteError, PRDifferException
from tests.unit.infrastructure.test_gitlab_operations import (
    FakeGitLab,
    FakeMergeRequests,
    FakeProject,
    FakeProjects,
    _payload,
    _version,
    select,
)


@pytest.mark.unit
class TestPinnedVersionRecords:
    def test_records_preserve_pinned_version_order(self) -> None:
        refs = {"base_sha": "b", "start_sha": "s", "head_sha": "h"}
        diffs = [
            {
                "old_path": "one.py",
                "new_path": "one.py",
                "a_mode": "100644",
                "b_mode": "100644",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "+x",
                "collapsed": False,
                "too_large": False,
            },
            {
                "old_path": "two.py",
                "new_path": "two.py",
                "a_mode": "100644",
                "b_mode": "100644",
                "new_file": True,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "+y",
                "collapsed": False,
                "too_large": False,
            },
        ]
        versions = [_version(1, "b", "s", "h")]
        payloads = {1: _payload(1, "b", "s", "h", real_size=2, diffs=diffs)}
        mr = FakeMergeRequests(diff_refs=refs, versions=versions, version_payloads=payloads)

        records = select(FakeGitLab(FakeProjects(FakeProject(mr))), "owner/repo", 17).records

        assert [r.new_path for r in records] == ["one.py", "two.py"]
        assert records[1].new_file is True
        assert mr.diffs.list_calls == 1
        assert mr.diffs.get_calls == [1]


class _PageTwoFailVersions:
    """Iterable that yields one version then fails (later-page materialization)."""

    def __init__(self, first: dict) -> None:
        self._first = first
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        yield self._first
        import requests

        raise requests.ConnectionError("diff version list page 2 failed")


class FailingPageDiffsManager:
    def __init__(self, first_version: dict) -> None:
        self._first = first_version
        self.list_calls = 0
        self.get_calls: list[int] = []
        self.last_get_all: bool | None = None

    def list(self, get_all: bool = False, **kwargs):
        self.list_calls += 1
        self.last_get_all = get_all
        return _PageTwoFailVersions(self._first)

    def get(self, version_id: int):
        self.get_calls.append(version_id)
        raise AssertionError("version get must not run after list pagination failure")


@pytest.mark.unit
class TestDiffVersionListPaginationFailure:
    def test_later_page_failure_is_operational_and_no_snapshot(self) -> None:
        from prdiffer.domain.error_codes import E5019_CONNECTION_ERROR

        refs = {"base_sha": "b", "start_sha": "s", "head_sha": "h"}
        first = _version(9, "b", "s", "h")
        diffs = FailingPageDiffsManager(first)
        mr = FakeMergeRequests(diff_refs=refs, versions=[first], version_payloads={})
        mr.diffs = diffs  # type: ignore[assignment]

        with pytest.raises(PRDifferException) as exc:
            select(FakeGitLab(FakeProjects(FakeProject(mr))), "owner/repo", 3)
        assert not isinstance(exc.value, FullDiffIncompleteError)
        assert exc.value.error_code is E5019_CONNECTION_ERROR
        assert diffs.list_calls == 1
        assert diffs.last_get_all is True
        assert diffs.get_calls == []
