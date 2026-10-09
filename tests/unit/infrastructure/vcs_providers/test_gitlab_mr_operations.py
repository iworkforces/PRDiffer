"""Unit tests for GitLab MR approve and description update operations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock

import gitlab
import pytest
import requests

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.error_codes import (
    E1001_INVALID_URL,
    E1011_HEAD_SHA_MISMATCH,
    E2006_GITLAB_AUTH_FAILED,
    E2007_GITLAB_INSUFFICIENT_PERMISSIONS,
    E3006_GITLAB_RATE_LIMITED,
    E4001_REPO_NOT_FOUND,
    E4002_PR_NOT_FOUND,
    E5004_TIMEOUT_ERROR,
    E5019_CONNECTION_ERROR,
    E5021_GITLAB_API_ERROR,
)
from prdiffer.domain.exceptions import (
    AuthenticationError,
    AuthorizationError,
    GitLabAPIError,
    HeadSHAMismatchError,
    RateLimitError,
    TimeoutError as DomainTimeoutError,
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


HEAD_SHA = "a" * 40
MOVED_HEAD_SHA = "b" * 40
NOTE_ID = 987
SINGLE_ATTEMPT_KWARGS = {"retry_transient_errors": False, "max_retries": 0, "obey_rate_limit": False}


class FakeNote:
    def __init__(self, body: str, note_id: int | None = NOTE_ID, *, delete_error: Exception | None = None) -> None:
        self.id = note_id
        self.body = body
        self._delete_error = delete_error
        self.delete_calls: list[dict[str, Any]] = []

    def delete(self, **kwargs: Any) -> None:
        self.delete_calls.append(kwargs)
        if self._delete_error is not None:
            raise self._delete_error


class FakeNotes:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.create_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.create_error: Exception | None = None
        self.note_id: int | None = NOTE_ID
        self.delete_error: Exception | None = None
        self.notes: list[FakeNote] = []

    def create(self, *args: Any, **kwargs: Any) -> Any:
        self.create_calls.append((args, kwargs))
        if self.create_error is not None:
            raise self.create_error
        payload = args[0]
        self.created.append(payload)
        note = FakeNote(payload["body"], self.note_id, delete_error=self.delete_error)
        self.notes.append(note)
        return note


class FakeMergeRequest:
    def __init__(self, sha: object = HEAD_SHA) -> None:
        self.approved = False
        self.description = ""
        self.saved = False
        self.notes = FakeNotes()
        self.sha = sha
        # diff_refs is diff-version metadata; head-bound approval must not trust it.
        self.diff_refs = {"base_sha": "0" * 40, "start_sha": "0" * 40, "head_sha": HEAD_SHA}
        self.approve_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.approve_error: Exception | None = None

    def approve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.approve_calls.append((args, kwargs))
        if self.approve_error is not None:
            raise self.approve_error
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


def _approval_conflict() -> Exception:
    return gitlab.exceptions.GitlabMRApprovalError("SHA does not match HEAD of source branch", 409, b"{}")


class TestGitLabOperationsHeadBoundApprove:
    def test_pre_note_mismatch_creates_no_note_and_does_not_approve(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        merge_request.sha = MOVED_HEAD_SHA.upper()
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        assert exc_info.value.error_code is E1011_HEAD_SHA_MISMATCH
        assert exc_info.value.details == {"expected_head_sha": HEAD_SHA, "actual_head_sha": MOVED_HEAD_SHA}
        assert merge_request.notes.create_calls == []
        assert merge_request.approve_calls == []
        assert project_gets(client) == [1]

    def test_stale_diff_refs_do_not_mask_moved_head(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        merge_request.sha = MOVED_HEAD_SHA
        assert merge_request.diff_refs["head_sha"] == HEAD_SHA
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        assert exc_info.value.actual_head_sha == MOVED_HEAD_SHA
        assert merge_request.notes.create_calls == []
        assert merge_request.approve_calls == []

    def test_missing_head_sha_fails_before_note(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        delattr(merge_request, "sha")
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(GitLabAPIError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        assert exc_info.value.error_code is E5021_GITLAB_API_ERROR
        assert exc_info.value.details == {"reason": "head_sha_unavailable"}
        assert merge_request.notes.create_calls == []
        assert merge_request.approve_calls == []

    @pytest.mark.parametrize("bad_sha", [None, 12345, b"a" * 40, "", "g" * 40, "a" * 39, "a" * 41, "not-a-sha"])
    def test_malformed_head_sha_fails_before_note(self, ops: GitLabOperations, bad_sha: object) -> None:
        merge_request = FakeMergeRequest(sha=bad_sha)
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(GitLabAPIError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        assert exc_info.value.error_code is E5021_GITLAB_API_ERROR
        assert "reason" in (exc_info.value.details or {})
        assert merge_request.notes.create_calls == []
        assert merge_request.approve_calls == []

    @pytest.mark.parametrize("sha", ["a" * 40, "A" * 40, "c" * 64])
    def test_match_creates_note_then_approves_with_sha_and_single_attempt_kwargs(self, ops: GitLabOperations, sha: str) -> None:
        merge_request = FakeMergeRequest(sha=sha)
        client = FakeClient(FakeProject(merge_request))
        order: list[str] = []
        original_create = merge_request.notes.create
        original_approve = merge_request.approve

        def create_tracking(*args: Any, **kwargs: Any) -> FakeNote:
            order.append("note")
            return original_create(*args, **kwargs)

        def approve_tracking(*args: Any, **kwargs: Any) -> dict[str, Any]:
            order.append("approve")
            return original_approve(*args, **kwargs)

        setattr(merge_request.notes, "create", create_tracking)
        setattr(merge_request, "approve", approve_tracking)

        result = ops.approve_with_client(as_gitlab(client), "group/project", 42, "Great work!", expected_head_sha=sha.lower())

        assert order == ["note", "approve"]
        assert merge_request.notes.create_calls == [(({"body": "Great work!"},), SINGLE_ATTEMPT_KWARGS)]
        assert merge_request.approve_calls == [((), {"sha": sha.lower(), **SINGLE_ATTEMPT_KWARGS})]
        assert merge_request.notes.notes[0].delete_calls == []
        assert result == "Successfully approved MR !42 in group/project"

    def test_approval_conflict_deletes_only_our_note(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        merge_request.approve_error = _approval_conflict()
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        error = exc_info.value
        assert error.error_code is E1011_HEAD_SHA_MISMATCH
        assert error.compliment_note == "deleted"
        assert error.actual_head_sha is None
        assert error.details == {"expected_head_sha": HEAD_SHA, "compliment_note": "deleted"}
        assert error.__cause__ is merge_request.approve_error
        assert len(merge_request.notes.notes) == 1
        assert merge_request.notes.notes[0].delete_calls == [SINGLE_ATTEMPT_KWARGS]
        assert len(merge_request.approve_calls) == 1
        assert merge_request.approved is False

    def test_approval_conflict_detected_by_response_code_alone(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        merge_request.approve_error = FakeGitlabError("conflict", response_code=409)
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        assert exc_info.value.compliment_note == "deleted"

    def test_approval_conflict_with_failed_cleanup_still_reports_mismatch(
        self, ops: GitLabOperations, merge_request: FakeMergeRequest, caplog: pytest.LogCaptureFixture
    ) -> None:
        merge_request.approve_error = _approval_conflict()
        merge_request.notes.delete_error = FakeGitlabError("cannot delete", response_code=502)
        client = FakeClient(FakeProject(merge_request))

        with caplog.at_level(logging.WARNING):
            with pytest.raises(HeadSHAMismatchError) as exc_info:
                ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        error = exc_info.value
        assert error.compliment_note == "cleanup_failed"
        assert error.actual_head_sha is None
        assert error.details == {"expected_head_sha": HEAD_SHA, "compliment_note": "cleanup_failed"}
        assert error.__cause__ is merge_request.approve_error
        assert merge_request.notes.notes[0].delete_calls == [SINGLE_ATTEMPT_KWARGS]
        assert any("compliment note" in record.getMessage() and "status=502" in record.getMessage() for record in caplog.records)

    def test_created_note_without_id_is_not_deleted(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        merge_request.approve_error = _approval_conflict()
        merge_request.notes.note_id = None
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        assert exc_info.value.compliment_note == "cleanup_failed"
        assert merge_request.notes.notes[0].delete_calls == []

    def test_note_creation_conflict_is_mapped_not_mismatch(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        merge_request.notes.create_error = gitlab.exceptions.GitlabCreateError("conflict", 409, b"{}")
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(GitLabAPIError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        assert exc_info.value.error_code is E5021_GITLAB_API_ERROR
        assert merge_request.approve_calls == []
        assert merge_request.notes.notes == []

    @pytest.mark.parametrize(
        ("error_factory", "expected_type", "expected_code"),
        [
            (lambda: requests.exceptions.ReadTimeout("read timed out"), DomainTimeoutError, E5004_TIMEOUT_ERROR),
            (lambda: requests.exceptions.ConnectionError("refused"), GitLabAPIError, E5019_CONNECTION_ERROR),
            (lambda: gitlab.exceptions.GitlabMRApprovalError("boom", 502, b"{}"), GitLabAPIError, E5021_GITLAB_API_ERROR),
            (lambda: FakeGitlabError("unavailable", response_code=503), GitLabAPIError, E5021_GITLAB_API_ERROR),
            (lambda: FakeGitlabError("slow down", response_code=429), RateLimitError, E3006_GITLAB_RATE_LIMITED),
            (lambda: FakeGitlabError("nope", response_code=403), AuthorizationError, E2007_GITLAB_INSUFFICIENT_PERMISSIONS),
        ],
    )
    def test_ambiguous_approve_failures_are_mapped_without_cleanup_or_retry(
        self,
        ops: GitLabOperations,
        merge_request: FakeMergeRequest,
        error_factory: Callable[[], Exception],
        expected_type: type[Exception],
        expected_code: object,
    ) -> None:
        merge_request.approve_error = error_factory()
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(expected_type) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "Nice", expected_head_sha=HEAD_SHA)

        assert getattr(exc_info.value, "error_code") is expected_code
        assert not isinstance(exc_info.value, HeadSHAMismatchError)
        assert len(merge_request.approve_calls) == 1
        assert len(merge_request.notes.notes) == 1
        assert merge_request.notes.notes[0].delete_calls == []

    def test_empty_compliment_rejected_before_any_sdk_call_with_sha(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(ValidationError):
            ops.approve_with_client(as_gitlab(client), "group/project", 1, "  ", expected_head_sha=HEAD_SHA)

        assert client.projects.get_calls == []

    def test_no_sha_path_keeps_call_shapes(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        merge_request.sha = MOVED_HEAD_SHA  # irrelevant without expected_head_sha
        client = FakeClient(FakeProject(merge_request))

        result = ops.approve_with_client(as_gitlab(client), "group/project", 5, "Nice")

        assert merge_request.notes.create_calls == [(({"body": "Nice"},), {})]
        assert merge_request.approve_calls == [((), {})]
        assert merge_request.notes.notes[0].delete_calls == []
        assert result == "Successfully approved MR !5 in group/project"

    def test_no_sha_path_conflict_still_maps_to_generic_api_error(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
        merge_request.approve_error = _approval_conflict()
        client = FakeClient(FakeProject(merge_request))

        with pytest.raises(GitLabAPIError) as exc_info:
            ops.approve_with_client(as_gitlab(client), "group/project", 5, "Nice")

        assert exc_info.value.error_code is E5021_GITLAB_API_ERROR
        assert merge_request.notes.notes[0].delete_calls == []


def as_gitlab(client: FakeClient) -> gitlab.Gitlab:
    return cast(gitlab.Gitlab, client)


def project_gets(client: FakeClient) -> list[int]:
    project = client.projects._project
    assert project is not None
    return project.mergerequests.get_calls


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

    def test_update_description_rejects_whitespace(self, ops: GitLabOperations, merge_request: FakeMergeRequest) -> None:
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
    async def test_approve_pr_with_comment_nested_namespace_and_custom_host(self, merge_request: FakeMergeRequest) -> None:
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

    @pytest.mark.asyncio
    async def test_approve_pr_with_comment_forwards_expected_head_sha(self, merge_request: FakeMergeRequest) -> None:
        repo = _repository_for(merge_request)

        result = await repo.approve_pr_with_comment("owner", "repo", 17, "Solid", base_url="https://gitlab.com", expected_head_sha=HEAD_SHA)

        assert result == "Successfully approved MR !17 in owner/repo"
        assert merge_request.approve_calls == [((), {"sha": HEAD_SHA, **SINGLE_ATTEMPT_KWARGS})]

    @pytest.mark.asyncio
    async def test_approve_pr_with_comment_without_sha_keeps_legacy_calls(self, merge_request: FakeMergeRequest) -> None:
        repo = _repository_for(merge_request)

        await repo.approve_pr_with_comment("owner", "repo", 17, "Solid", base_url="https://gitlab.com")

        assert merge_request.notes.create_calls == [(({"body": "Solid"},), {})]
        assert merge_request.approve_calls == [((), {})]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cleanup_fails", [False, True])
    async def test_head_sha_mismatch_passes_through_runtime_unchanged(self, merge_request: FakeMergeRequest, cleanup_fails: bool) -> None:
        merge_request.approve_error = _approval_conflict()
        if cleanup_fails:
            merge_request.notes.delete_error = FakeGitlabError("nope", response_code=500)
        repo = _repository_for(merge_request)

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            await repo.approve_pr_with_comment("owner", "repo", 17, "Solid", base_url="https://gitlab.com", expected_head_sha=HEAD_SHA)

        assert exc_info.value.error_code is E1011_HEAD_SHA_MISMATCH
        assert exc_info.value.compliment_note == ("cleanup_failed" if cleanup_fails else "deleted")

    @pytest.mark.asyncio
    async def test_pre_note_head_mismatch_passes_through_runtime_unchanged(self, merge_request: FakeMergeRequest) -> None:
        merge_request.sha = MOVED_HEAD_SHA
        repo = _repository_for(merge_request)

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            await repo.approve_pr_with_comment("owner", "repo", 17, "Solid", base_url="https://gitlab.com", expected_head_sha=HEAD_SHA)

        assert exc_info.value.details == {"expected_head_sha": HEAD_SHA, "actual_head_sha": MOVED_HEAD_SHA}
        assert merge_request.notes.create_calls == []


def _repository_for(merge_request: FakeMergeRequest) -> GitLabVCSRepository:
    config = GitLabConfig(allowed_hosts=("gitlab.com",))
    project = FakeProject(merge_request)

    def client_factory(url: str, private_token: str | None = None, **kwargs: Any) -> FakeClient:
        return FakeClient(project)

    runtime = GitLabRuntime(config, private_token="t", client_factory=cast(Callable[..., gitlab.Gitlab], client_factory))
    return GitLabVCSRepository("t", config=config, runtime=runtime, operations=GitLabOperations(), session_reader=MagicMock())
