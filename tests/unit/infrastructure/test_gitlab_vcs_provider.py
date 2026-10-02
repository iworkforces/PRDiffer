"""Tests for the GitLab adapter's strict session-reader contract."""

from __future__ import annotations

from prdiffer.domain.interfaces.pr_diff_reader import SessionPRDiffReader
from prdiffer.infrastructure.vcs_providers.gitlab_repository import GitLabVCSRepository


class TestGitLabVCSRepository:
    def test_is_session_pr_diff_reader(self) -> None:
        # MCP composition rejects GitLab readers that fail this runtime protocol check.
        assert isinstance(GitLabVCSRepository(), SessionPRDiffReader)
