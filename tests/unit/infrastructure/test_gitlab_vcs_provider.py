"""Tests for GitLab VCS repository URL support and file mapping helpers."""

from __future__ import annotations


import pytest

from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.error_codes import E5002_GITHUB_API_ERROR
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.infrastructure.vcs_providers.gitlab_models import GitLabDiffRecord
from prdiffer.infrastructure.vcs_providers.gitlab_repository import GitLabVCSRepository


class TestGitLabVCSRepository:
    def test_provider_metadata_and_url_support(self) -> None:
        provider = GitLabVCSRepository()
        assert provider.provider_name == "gitlab"
        assert provider.provider_version == "v4"
        assert provider.supports_repository("https://gitlab.com/owner/repo/-/merge_requests/17")
        assert provider.supports_repository("https://gitlab.com/group/subgroup/project/-/merge_requests/42")
        assert provider.supports_repository(
            "https://gitlab.example.com/trace-analysis/oh-my-grokbuild/-/merge_requests/1"
        )
        assert provider.supports_repository("https://gitlab.com/owner/repo/-/tree/abc123")
        assert not provider.supports_repository("https://github.com/owner/repo/pull/17")
        assert not provider.supports_repository("https://gitlab.com/group/../project/-/merge_requests/1")
        assert not provider.supports_repository("http://gitlab.example.com/a/b/-/merge_requests/1")

    def test_to_file_diff_mapping_preserves_semantics(self) -> None:
        added_diff = "--- /dev/null\n+++ b/src/added.py\n+first\n+second"
        modified_diff = "--- a/src/current.py\n+++ b/src/current.py\n-old\n+new"
        renamed_diff = "--- a/src/old.py\n+++ b/src/new.py\n-old\n+new"
        deleted_diff = "--- a/src/deleted.py\n+++ /dev/null\n-old"
        records = (
            GitLabDiffRecord(old_path="src/added.py", new_path="src/added.py", new_file=True, deleted_file=False, renamed_file=False, diff=added_diff),
            GitLabDiffRecord(old_path="src/current.py", new_path="src/current.py", new_file=False, deleted_file=False, renamed_file=False, diff=modified_diff),
            GitLabDiffRecord(old_path="src/old.py", new_path="src/new.py", new_file=False, deleted_file=False, renamed_file=True, diff=renamed_diff),
            GitLabDiffRecord(
                old_path="src/deleted.py", new_path="src/replacement.py", new_file=False, deleted_file=True, renamed_file=False, diff=deleted_diff
            ),
            GitLabDiffRecord(old_path="src/collapsed.py", new_path="src/collapsed.py", new_file=False, deleted_file=False, renamed_file=False, collapsed=True),
            GitLabDiffRecord(old_path="src/large.py", new_path="src/large.py", new_file=False, deleted_file=False, renamed_file=False, too_large=True),
        )
        files = tuple(GitLabVCSRepository._to_file_diff(r) for r in records)
        assert files[0] == FileDiffResponse("src/added.py", EDIT_TYPE.ADDED, FileStats(additions=2, deletions=0), added_diff)
        assert files[2].previous_path == "src/old.py"
        assert files[2].status is EDIT_TYPE.RENAMED
        assert files[4].diff == ""
        assert files[5].diff == ""

    def test_conflicting_diff_flags_raise(self) -> None:
        record = GitLabDiffRecord(old_path="src/file.py", new_path="src/file.py", new_file=True, deleted_file=True, renamed_file=False)
        with pytest.raises(PRDifferException) as error:
            GitLabVCSRepository._to_file_diff(record)
        assert error.value.message == "GitLab diff record has conflicting change flags"
        assert error.value.error_code is E5002_GITHUB_API_ERROR

    def test_open_pr_diff_session_method_exists(self) -> None:
        provider = GitLabVCSRepository()
        assert callable(getattr(type(provider), "open_pr_diff_session", None))
