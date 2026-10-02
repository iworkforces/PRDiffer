"""Unit tests for GitLab MR approve and description update operations."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.error_codes import (
    E1001_INVALID_URL,
    E2006_GITLAB_AUTH_FAILED,
    E2007_GITLAB_INSUFFICIENT_PERMISSIONS,
    E3006_GITLAB_RATE_LIMITED,
    E4001_REPO_NOT_FOUND,
    E4002_PR_NOT_FOUND,
    E5021_GITLAB_API_ERROR,
)
from prdiffer.domain.exceptions import (
    AuthenticationError,
    AuthorizationError,
    GitLabAPIError,
    RateLimitError,
    ValidationError,
)
from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations
from prdiffer.infrastructure.vcs_providers.gitlab_repository import GitLabVCSRepository
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import GitLabRuntime


class FakeGitlabError(Exception):
    def __init__(self, message: str = "", response_code: int | None = None) -> None:
        super().__init__(message)
        self.response_code = response_code
        self.error_message = message


class FakeNotes:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.created.append(payload)
        return payload


class FakeMergeRequest:
    def __init__(self) -> None:
        self.approved = False
        self.description = ""
        self.saved = False
        self.notes = FakeNotes()

    def approve(self) -> dict[str, Any]:
        self.approved = True
        return {"approved": True}

    def save(self) -> None:
        self.saved = True


class FakeMergeRequests:
    def __init__(self, merge_request: FakeMergeRequest, *, get_error: Exception | None = None) -> None:
        self._mr = merge_request
        self._get_error = get_error
        self.get_calls: list[int] = []

    def get(self, iid: int) -> FakeMergeRequest:
        self.get_calls.append(iid)
        if self._get_error is not None:
            raise self._get_error
        return self._mr


class FakeProject:
    def __init__(self, merge_request: FakeMergeRequest, *, mr_error: Exception | None = None) -> None:
        self.mergerequests = FakeMergeRequests(merge_request, get_error=mr_error)


class FakeProjects:
    def __init__(
        self,
        project: FakeProject | None = None,
        *,
        get_error: Exception | None = None,
    ) -> None:
        self._project = project
        self._get_error = get_error
        self.get_calls: list[str] = []

    def get(self, path: str) -> FakeProject:
        self.get_calls.append(path)
        if self._get_error is not None:
            raise self._get_error
        assert self._project is not None
        return self._project


class FakeClient:
    def __init__(
        self,
        project: FakeProject | None = None,
        *,
        project_error: Exception | None = None,
    ) -> None:
        self.projects = FakeProjects(project, get_error=project_error)
        self.session = MagicMock()
        self.session.close = MagicMock()


@pytest.fixture
def ops() -> GitLabOperations:
    return GitLabOperations()


@pytest.fixture
def merge_request() -> FakeMergeRequest:
    return FakeMergeRequest()


class TestGitLabOperationsApprove:
    def test_approve_with_client_success(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        project = FakeProject(merge_request)
        client = FakeClient(project)
        order: list[str] = []

        original_create = merge_request.notes.create
        original_approve = merge_request.approve

        def create_tracking(payload: dict[str, Any]) -> dict[str, Any]:
            order.append("note")
            return original_create(payload)

        def approve_tracking() -> dict[str, Any]:
            order.append("approve")
            return original_approve()

        setattr(merge_request.notes, "create", create_tracking)
        setattr(merge_request, "approve", approve_tracking)

        result = ops.approve_with_client(client, "group/project", 42, "Great work!")

        assert merge_request.approved is True
        assert merge_request.notes.created == [{"body": "Great work!"}]
        assert order == ["note", "approve"], "note must land before approve to avoid approved-without-note"
        assert "Successfully approved MR !42" in result
        assert "group/project" in result
        assert project.mergerequests.get_calls == [42]
        assert client.projects.get_calls == ["group/project"]

    def test_approve_failure_after_note_leaves_mr_unapproved(
        self, ops: GitLabOperations, merge_request: FakeMergeRequest
    ) -> None:
        """If approve fails after note, MR must not be marked approved (note-first order)."""

        def boom() -> dict[str, Any]:
            raise FakeGitlabError("approve blocked", response_code=403)

        setattr(merge_request, "approve", boom)
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(AuthorizationError) as exc_info:
            ops.approve_with_client(client, "group/project", 1, "Nice note first")

        assert exc_info.value.error_code is E2007_GITLAB_INSUFFICIENT_PERMISSIONS
        assert merge_request.notes.created == [{"body": "Nice note first"}]
        assert merge_request.approved is False

    def test_note_failure_before_approve_does_not_approve(
        self, ops: GitLabOperations, merge_request: FakeMergeRequest
    ) -> None:
        def boom_note(payload: dict[str, Any]) -> dict[str, Any]:
            raise FakeGitlabError("notes disabled", response_code=403)

        setattr(merge_request.notes, "create", boom_note)
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(AuthorizationError):
            ops.approve_with_client(client, "group/project", 1, "Will not approve")

        assert merge_request.approved is False
        assert merge_request.notes.created == []

    def test_approve_rejects_empty_compliment(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(ValidationError) as exc_info:
            ops.approve_with_client(client, "group/project", 1, "")

        assert exc_info.value.error_code is E1001_INVALID_URL
        assert merge_request.approved is False
        assert merge_request.notes.created == []

    def test_approve_rejects_whitespace_only_compliment(
        self, ops: GitLabOperations, merge_request: FakeMergeRequest
    ) -> None:
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(ValidationError):
            ops.approve_with_client(client, "group/project", 1, "   ")

    def test_approve_rejects_non_string_compliment(
        self, ops: GitLabOperations, merge_request: FakeMergeRequest
    ) -> None:
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(ValidationError):
            ops.approve_with_client(client, "group/project", 1, cast(str, 123))

    def test_approve_project_not_found(self, ops: GitLabOperations) -> None:
        client = FakeClient(project_error=FakeGitlabError("missing", response_code=404))

        with pytest.raises(GitLabAPIError) as exc_info:
            ops.approve_with_client(client, "missing/proj", 1, "Nice")

        assert exc_info.value.error_code is E4001_REPO_NOT_FOUND

    def test_approve_mr_not_found(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(FakeProject(merge_request, mr_error=FakeGitlabError("no mr", response_code=404)))

        with pytest.raises(GitLabAPIError) as exc_info:
            ops.approve_with_client(client, "group/project", 99, "Nice")

        assert exc_info.value.error_code is E4002_PR_NOT_FOUND

    def test_approve_auth_failed(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(project_error=FakeGitlabError("auth", response_code=401))

        with pytest.raises(AuthenticationError) as exc_info:
            ops.approve_with_client(client, "group/project", 1, "Nice")

        assert exc_info.value.error_code is E2006_GITLAB_AUTH_FAILED

    def test_approve_forbidden(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(project_error=FakeGitlabError("nope", response_code=403))

        with pytest.raises(AuthorizationError) as exc_info:
            ops.approve_with_client(client, "group/project", 1, "Nice")

        assert exc_info.value.error_code is E2007_GITLAB_INSUFFICIENT_PERMISSIONS

    def test_approve_rate_limited(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(project_error=FakeGitlabError("slow down", response_code=429))

        with pytest.raises(RateLimitError) as exc_info:
            ops.approve_with_client(client, "group/project", 1, "Nice")

        assert exc_info.value.error_code is E3006_GITLAB_RATE_LIMITED

    def test_approve_server_error_on_approve_call(
        self, ops: GitLabOperations, merge_request: FakeMergeRequest
    ) -> None:
        def boom() -> dict[str, Any]:
            raise FakeGitlabError("boom", response_code=502)

        setattr(merge_request, "approve", boom)
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(GitLabAPIError) as exc_info:
            ops.approve_with_client(client, "group/project", 1, "Nice")

        assert exc_info.value.error_code is E5021_GITLAB_API_ERROR
        # Note-first: note may exist while approve 502s — MR still not approved.
        assert merge_request.approved is False
        assert merge_request.notes.created == [{"body": "Nice"}]


class TestGitLabOperationsUpdateDescription:
    def test_update_description_success(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(FakeProject(merge_request))

        result = ops.update_description_with_client(client, "group/project", 7, "New body")

        assert merge_request.description == "New body"
        assert merge_request.saved is True
        assert "Successfully updated description for MR !7" in result
        assert "group/project" in result

    def test_update_description_rejects_empty(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(ValidationError) as exc_info:
            ops.update_description_with_client(client, "group/project", 1, "")

        assert exc_info.value.error_code is E1001_INVALID_URL
        assert merge_request.saved is False

    def test_update_description_rejects_whitespace(
        self, ops: GitLabOperations, merge_request: FakeMergeRequest
    ) -> None:
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(ValidationError):
            ops.update_description_with_client(client, "group/project", 1, "  \n")

    def test_update_description_mr_not_found(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(FakeProject(merge_request, mr_error=FakeGitlabError("gone", response_code=404)))

        with pytest.raises(GitLabAPIError) as exc_info:
            ops.update_description_with_client(client, "group/project", 3, "Body")

        assert exc_info.value.error_code is E4002_PR_NOT_FOUND


class TestGitLabVCSRepositoryMROps:
    @pytest.mark.asyncio
    async def test_approve_pr_with_comment_uses_runtime(self, merge_request: FakeMergeRequest) -> None:
        config = GitLabConfig(allowed_hosts=("gitlab.com",))
        ops = GitLabOperations()
        project = FakeProject(merge_request)

        def client_factory(url: str, private_token: str | None = None, **kwargs: Any) -> FakeClient:
            return FakeClient(project)

        runtime = GitLabRuntime(config, private_token="t", client_factory=client_factory)
        repo = GitLabVCSRepository(
            "t",
            config=config,
            runtime=runtime,
            operations=ops,
            session_reader=MagicMock(),
        )

        result = await repo.approve_pr_with_comment(
            "owner",
            "repo",
            17,
            "Solid refactor",
            base_url="https://gitlab.com",
        )

        assert merge_request.approved is True
        assert merge_request.notes.created == [{"body": "Solid refactor"}]
        assert "Successfully approved MR !17" in result
        assert "owner/repo" in result

    @pytest.mark.asyncio
    async def test_approve_pr_with_comment_nested_namespace_and_custom_host(
        self, merge_request: FakeMergeRequest
    ) -> None:
        config = GitLabConfig(allowed_hosts=("gitlab.com", "gitlab.example.com"))
        ops = GitLabOperations()
        project = FakeProject(merge_request)
        clients: list[FakeClient] = []

        def client_factory(url: str, private_token: str | None = None, **kwargs: Any) -> FakeClient:
            client = FakeClient(project)
            setattr(client, "constructed_url", url)
            clients.append(client)
            return client

        runtime = GitLabRuntime(config, private_token="t", client_factory=client_factory)
        repo = GitLabVCSRepository(
            "t",
            config=config,
            runtime=runtime,
            operations=ops,
            session_reader=MagicMock(),
        )

        result = await repo.approve_pr_with_comment(
            "group/sub",
            "project",
            3,
            "Nested",
            base_url="https://gitlab.example.com",
        )

        assert "Successfully approved MR !3" in result
        assert "group/sub/project" in result
        assert len(clients) == 1
        assert "gitlab.example.com" in getattr(clients[0], "constructed_url")
        assert clients[0].projects.get_calls == ["group/sub/project"]
        assert project.mergerequests.get_calls == [3]

    @pytest.mark.asyncio
    async def test_update_pr_description_uses_runtime(self, merge_request: FakeMergeRequest) -> None:
        config = GitLabConfig(allowed_hosts=("gitlab.com",))
        ops = GitLabOperations()
        project = FakeProject(merge_request)

        def client_factory(url: str, private_token: str | None = None, **kwargs: Any) -> FakeClient:
            return FakeClient(project)

        runtime = GitLabRuntime(config, private_token="t", client_factory=client_factory)
        repo = GitLabVCSRepository(
            "t",
            config=config,
            runtime=runtime,
            operations=ops,
            session_reader=MagicMock(),
        )

        result = await repo.update_pr_description(
            "owner",
            "repo",
            9,
            "Updated description",
            base_url="https://gitlab.com",
        )

        assert merge_request.description == "Updated description"
        assert merge_request.saved is True
        assert "Successfully updated description for MR !9" in result
