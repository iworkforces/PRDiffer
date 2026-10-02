"""Tests for immutable GitLab MR diff version selection."""

from __future__ import annotations

from typing import Any, cast

import pytest

from prdiffer.domain.error_codes import E4001_REPO_NOT_FOUND, E4002_PR_NOT_FOUND
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason, PRDifferException
import prdiffer.infrastructure.vcs_providers.gitlab_operations as gitlab_operations
from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations


class FakeDiffsManager:
    def __init__(
        self,
        versions: list[dict[str, Any]],
        version_payloads: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self._versions = versions
        self._version_payloads = version_payloads or {}
        self.list_calls = 0
        self.get_calls: list[int] = []
        self.current_diffs_calls = 0

    def list(self, get_all: bool = False, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls += 1
        return list(self._versions)

    def get(self, version_id: int) -> Any:
        self.get_calls.append(version_id)
        payload = self._version_payloads.get(version_id)
        if payload is None:
            raise gitlab_operations.gitlab.GitlabGetError("missing", response_code=404)
        return type("Version", (), payload)()


class FakeMergeRequests:
    def __init__(
        self,
        *,
        diff_refs: dict[str, str] | None,
        versions: list[dict[str, Any]],
        version_payloads: dict[int, dict[str, Any]],
        error: BaseException | None = None,
        sha: str = "head",
    ) -> None:
        self._diff_refs = diff_refs
        self._error = error
        self._sha = sha
        self.calls: list[int] = []
        self.diffs = FakeDiffsManager(versions, version_payloads)

    def get(self, number: int) -> Any:
        self.calls.append(number)
        if self._error is not None:
            raise self._error
        return type(
            "MergeRequest",
            (),
            {
                "diff_refs": self._diff_refs,
                "sha": self._sha,
                "diffs": self.diffs,
            },
        )()


class FakeProject:
    def __init__(self, mr: FakeMergeRequests) -> None:
        self.mergerequests = mr


class FakeProjects:
    def __init__(self, project: FakeProject | None = None, error: BaseException | None = None) -> None:
        self._project = project
        self._error = error
        self.calls: list[str] = []

    def get(self, identifier: str) -> FakeProject:
        self.calls.append(identifier)
        if self._error is not None:
            raise self._error
        assert self._project is not None
        return self._project


class FakeGitLab:
    def __init__(self, projects: FakeProjects) -> None:
        self.projects = projects


def _version(vid: int, base: str, start: str, head: str) -> dict[str, Any]:
    return {
        "id": vid,
        "base_commit_sha": base,
        "start_commit_sha": start,
        "head_commit_sha": head,
    }


def _payload(
    vid: int,
    base: str,
    start: str,
    head: str,
    *,
    state: str = "collected",
    real_size: int | str | None = 1,
    diffs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if diffs is None:
        diffs = [
            {
                "old_path": "a.py",
                "new_path": "a.py",
                "a_mode": "100644",
                "b_mode": "100644",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "@@\n-a\n+b\n",
                "collapsed": False,
                "too_large": False,
            }
        ]
    return {
        "id": vid,
        "base_commit_sha": base,
        "start_commit_sha": start,
        "head_commit_sha": head,
        "state": state,
        "real_size": real_size,
        "diffs": diffs,
    }


def select(client: FakeGitLab, project_path: str, iid: int) -> Any:
    return GitLabOperations().select_with_client(cast(Any, client), project_path, iid)


@pytest.mark.unit
class TestSelectDiffSnapshot:
    def test_exact_match_independent_of_list_order(self) -> None:
        refs = {"base_sha": "b1", "start_sha": "s1", "head_sha": "h1"}
        versions = [
            _version(10, "old", "old", "old"),
            _version(99, "b1", "s1", "h1"),
            _version(5, "x", "y", "z"),
        ]
        # Shuffle-like reverse order still finds exact match
        versions = list(reversed(versions))
        payloads = {99: _payload(99, "b1", "s1", "h1", real_size=1)}
        mr = FakeMergeRequests(diff_refs=refs, versions=versions, version_payloads=payloads)
        client = FakeGitLab(FakeProjects(FakeProject(mr)))

        snapshot = select(client, "group/subgroup/project", 42)

        assert snapshot.version_id == 99
        assert snapshot.base_sha == "b1"
        assert snapshot.start_sha == "s1"
        assert snapshot.head_sha == "h1"
        assert snapshot.project_path == "group/subgroup/project"
        assert snapshot.iid == 42
        assert len(snapshot.records) == 1
        assert client.projects.calls == ["group/subgroup/project"]
        assert mr.diffs.list_calls == 1
        assert mr.diffs.get_calls == [99]
        assert mr.diffs.current_diffs_calls == 0

    def test_zero_match_fails_inventory(self) -> None:
        refs = {"base_sha": "b", "start_sha": "s", "head_sha": "h"}
        versions = [_version(1, "other", "s", "h")]
        mr = FakeMergeRequests(diff_refs=refs, versions=versions, version_payloads={})
        client = FakeGitLab(FakeProjects(FakeProject(mr)))

        with pytest.raises(FullDiffIncompleteError) as exc:
            select(client, "o/r", 1)
        assert exc.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED
        assert mr.diffs.get_calls == []

    def test_multiple_match_fails_inventory(self) -> None:
        refs = {"base_sha": "b", "start_sha": "s", "head_sha": "h"}
        versions = [_version(1, "b", "s", "h"), _version(2, "b", "s", "h")]
        mr = FakeMergeRequests(diff_refs=refs, versions=versions, version_payloads={})
        client = FakeGitLab(FakeProjects(FakeProject(mr)))

        with pytest.raises(FullDiffIncompleteError) as exc:
            select(client, "o/r", 1)
        assert exc.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED
        assert mr.diffs.get_calls == []

    def test_missing_diff_refs_fails(self) -> None:
        mr = FakeMergeRequests(diff_refs=None, versions=[], version_payloads={})
        client = FakeGitLab(FakeProjects(FakeProject(mr)))
        with pytest.raises(FullDiffIncompleteError) as exc:
            select(client, "o/r", 1)
        assert exc.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED

    def test_fetched_version_drift_fails(self) -> None:
        refs = {"base_sha": "b", "start_sha": "s", "head_sha": "h"}
        versions = [_version(7, "b", "s", "h")]
        # Fetched version has different head
        payloads = {7: _payload(7, "b", "s", "different")}
        mr = FakeMergeRequests(diff_refs=refs, versions=versions, version_payloads=payloads)
        client = FakeGitLab(FakeProjects(FakeProject(mr)))
        with pytest.raises(FullDiffIncompleteError) as exc:
            select(client, "o/r", 1)
        assert exc.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED

    def test_project_404_maps_e4001(self) -> None:
        err = gitlab_operations.gitlab.GitlabGetError("nf", response_code=404)
        client = FakeGitLab(FakeProjects(error=err))
        with pytest.raises(Exception) as exc:
            select(client, "missing/project", 1)
        code = getattr(exc.value, "error_code", None)
        assert code is E4001_REPO_NOT_FOUND or (isinstance(exc.value, PRDifferException) and "not found" in exc.value.message.lower())

    def test_mr_404_maps_e4002(self) -> None:
        err = gitlab_operations.gitlab.GitlabGetError("nf", response_code=404)
        mr = FakeMergeRequests(diff_refs={}, versions=[], version_payloads={}, error=err)
        client = FakeGitLab(FakeProjects(FakeProject(mr)))
        with pytest.raises(Exception) as exc:
            select(client, "o/r", 99)
        code = getattr(exc.value, "error_code", None)
        assert code is E4002_PR_NOT_FOUND


class TestParseGitlabRealSize:
    def test_none_and_empty(self):
        from prdiffer.infrastructure.vcs_providers.gitlab_operations import parse_gitlab_real_size

        assert parse_gitlab_real_size(None) is None
        assert parse_gitlab_real_size("") is None

    def test_valid_int_and_digit_string(self):
        from prdiffer.infrastructure.vcs_providers.gitlab_operations import parse_gitlab_real_size

        assert parse_gitlab_real_size(1) == 1
        assert parse_gitlab_real_size(0) == 0
        assert parse_gitlab_real_size("1") == 1
        assert parse_gitlab_real_size(" 12 ") == 12

    def test_rejects_boolean_true(self):
        from prdiffer.infrastructure.vcs_providers.gitlab_operations import parse_gitlab_real_size
        import pytest

        with pytest.raises(ValueError):
            parse_gitlab_real_size(True)
        with pytest.raises(ValueError):
            parse_gitlab_real_size(False)

    def test_rejects_float_negative_malformed(self):
        from prdiffer.infrastructure.vcs_providers.gitlab_operations import parse_gitlab_real_size
        import pytest

        with pytest.raises(ValueError):
            parse_gitlab_real_size(1.0)
        with pytest.raises(ValueError):
            parse_gitlab_real_size(-1)
        with pytest.raises(ValueError):
            parse_gitlab_real_size("1.5")
        with pytest.raises(ValueError):
            parse_gitlab_real_size("abc")
        with pytest.raises(ValueError):
            parse_gitlab_real_size([])
