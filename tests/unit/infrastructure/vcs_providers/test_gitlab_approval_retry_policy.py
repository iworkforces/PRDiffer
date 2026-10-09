"""Transport-backed proof that head-bound GitLab approvals never retry a mutation.

A real ``gitlab.Gitlab`` client (retry_transient_errors=True, runtime defaults injected by
``GitLabRuntime``) talks to an in-process requests adapter. The SHA path must issue exactly one
HTTP attempt per mutation; the legacy no-SHA path keeps the SDK retry policy.
"""

from __future__ import annotations

import json
import time
import types
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import unquote, urlparse

import gitlab
import gitlab.utils
import pytest
import requests
from requests.adapters import BaseAdapter

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.error_codes import (
    E1011_HEAD_SHA_MISMATCH,
    E3006_GITLAB_RATE_LIMITED,
    E5004_TIMEOUT_ERROR,
    E5019_CONNECTION_ERROR,
    E5021_GITLAB_API_ERROR,
)
from prdiffer.domain.exceptions import (
    GitLabAPIError,
    HeadSHAMismatchError,
    RateLimitError,
    TimeoutError as DomainTimeoutError,
)
from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations
from prdiffer.infrastructure.vcs_providers.gitlab_repository import GitLabVCSRepository
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import GitLabRuntime

HEAD_SHA = "a" * 40
MOVED_HEAD_SHA = "b" * 40
PROJECT_ID = 7
IID = 5
NOTE_ID = 4242
MR_PATH = f"/api/v4/projects/{PROJECT_ID}/merge_requests/{IID}"

Outcome = int | Exception


class FakeGitLabTransport(BaseAdapter):
    """Route python-gitlab requests to canned responses and record every attempt."""

    def __init__(
        self,
        *,
        mr_sha: str = HEAD_SHA,
        note_create: Outcome = 201,
        approve: Outcome = 201,
        note_delete: Outcome = 204,
    ) -> None:
        super().__init__()
        self.mr_sha = mr_sha
        self.outcomes: dict[tuple[str, str], Outcome] = {
            ("POST", f"{MR_PATH}/notes"): note_create,
            ("POST", f"{MR_PATH}/approve"): approve,
            ("DELETE", f"{MR_PATH}/notes/{NOTE_ID}"): note_delete,
        }
        self.requests: list[tuple[str, str, Any]] = []

    def attempts(self, method: str, path_suffix: str) -> list[Any]:
        return [body for verb, path, body in self.requests if verb == method and path.endswith(path_suffix)]

    def send(self, request: requests.PreparedRequest, *args: Any, **kwargs: Any) -> requests.Response:
        method = str(request.method)
        path = unquote(urlparse(str(request.url)).path)
        raw_body = request.body
        body = json.loads(raw_body) if isinstance(raw_body, (str, bytes, bytearray)) and raw_body else None
        self.requests.append((method, path, body))

        if method == "GET" and path == "/api/v4/projects/group/project":
            return self._response(request, 200, {"id": PROJECT_ID})
        if method == "GET" and path == MR_PATH:
            return self._response(request, 200, {"iid": IID, "project_id": PROJECT_ID, "sha": self.mr_sha})

        outcome = self.outcomes[(method, path)]
        if isinstance(outcome, Exception):
            raise outcome
        if outcome < 300:
            payload: dict[str, Any] = {"id": NOTE_ID, "body": "ok"} if path.endswith("/notes") else {"approved": True}
            return self._response(request, outcome, payload if outcome != 204 else None)
        headers = {"Retry-After": "1"} if outcome == 429 else {}
        return self._response(request, outcome, {"message": "provider said no"}, headers)

    def close(self) -> None:
        return None

    @staticmethod
    def _response(
        request: requests.PreparedRequest,
        status: int,
        payload: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response.reason = "stub"
        response.url = str(request.url)
        response.request = request
        response.headers.update(headers or {})
        if payload is not None:
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps(payload).encode()
        else:
            response._content = b""
        return response


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record SDK backoff sleeps without sleeping; any entry means the SDK retried."""
    recorded: list[float] = []
    monkeypatch.setattr(gitlab.utils, "time", types.SimpleNamespace(sleep=recorded.append, time=time.time))
    return recorded


def _repository(transport: FakeGitLabTransport, config: GitLabConfig) -> GitLabVCSRepository:
    def client_factory(url: str, **kwargs: Any) -> gitlab.Gitlab:
        session = requests.Session()
        session.mount("https://", transport)
        return gitlab.Gitlab(url, session=session, **kwargs)

    runtime = GitLabRuntime(config, private_token="token", client_factory=client_factory)
    return GitLabVCSRepository("token", config=config, runtime=runtime, operations=GitLabOperations(), session_reader=MagicMock())


@pytest.fixture
def config() -> GitLabConfig:
    cfg = GitLabConfig(allowed_hosts=("gitlab.com",))
    # Runtime defaults the policy must override: client retries on, SDK retries allowed.
    assert cfg.retry_transient_errors is True
    assert cfg.obey_rate_limit is True
    assert cfg.max_retries > 0
    return cfg


async def _approve(repo: GitLabVCSRepository, *, sha: str | None = HEAD_SHA) -> str:
    return await repo.approve_pr_with_comment("group", "project", IID, "Great work", base_url="https://gitlab.com", expected_head_sha=sha)


class TestHeadBoundApprovalRetryPolicy:
    @pytest.mark.asyncio
    async def test_success_issues_one_note_and_one_approve_with_sha(self, config: GitLabConfig, sleeps: list[float]) -> None:
        transport = FakeGitLabTransport()

        result = await _approve(_repository(transport, config))

        assert result == f"Successfully approved MR !{IID} in group/project"
        assert transport.attempts("POST", "/notes") == [{"body": "Great work"}]
        assert transport.attempts("POST", "/approve") == [{"sha": HEAD_SHA}]
        assert transport.attempts("DELETE", f"/notes/{NOTE_ID}") == []
        assert sleeps == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
    async def test_approve_5xx_is_attempted_once_and_note_is_kept(self, config: GitLabConfig, sleeps: list[float], status: int) -> None:
        transport = FakeGitLabTransport(approve=status)

        with pytest.raises(GitLabAPIError) as exc_info:
            await _approve(_repository(transport, config))

        assert exc_info.value.error_code is E5021_GITLAB_API_ERROR
        assert len(transport.attempts("POST", "/approve")) == 1
        assert len(transport.attempts("POST", "/notes")) == 1
        assert transport.attempts("DELETE", f"/notes/{NOTE_ID}") == []
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_approve_429_is_attempted_once_without_waiting(self, config: GitLabConfig, sleeps: list[float]) -> None:
        transport = FakeGitLabTransport(approve=429)

        with pytest.raises(RateLimitError) as exc_info:
            await _approve(_repository(transport, config))

        assert exc_info.value.error_code is E3006_GITLAB_RATE_LIMITED
        assert len(transport.attempts("POST", "/approve")) == 1
        assert transport.attempts("DELETE", f"/notes/{NOTE_ID}") == []
        assert sleeps == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error_factory", "expected_type", "expected_code"),
        [
            (lambda: requests.exceptions.ReadTimeout("read timed out"), DomainTimeoutError, E5004_TIMEOUT_ERROR),
            (lambda: requests.exceptions.ConnectionError("reset"), GitLabAPIError, E5019_CONNECTION_ERROR),
        ],
    )
    async def test_approve_transport_failure_is_attempted_once_and_note_is_kept(
        self,
        config: GitLabConfig,
        sleeps: list[float],
        error_factory: Callable[[], Exception],
        expected_type: type[Exception],
        expected_code: object,
    ) -> None:
        transport = FakeGitLabTransport(approve=error_factory())

        with pytest.raises(expected_type) as exc_info:
            await _approve(_repository(transport, config))

        assert getattr(exc_info.value, "error_code") is expected_code
        assert len(transport.attempts("POST", "/approve")) == 1
        assert transport.attempts("DELETE", f"/notes/{NOTE_ID}") == []
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_note_creation_5xx_is_attempted_once_and_never_approves(self, config: GitLabConfig, sleeps: list[float]) -> None:
        transport = FakeGitLabTransport(note_create=502)

        with pytest.raises(GitLabAPIError) as exc_info:
            await _approve(_repository(transport, config))

        assert exc_info.value.error_code is E5021_GITLAB_API_ERROR
        assert len(transport.attempts("POST", "/notes")) == 1
        assert transport.attempts("POST", "/approve") == []
        assert transport.attempts("DELETE", f"/notes/{NOTE_ID}") == []
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_pre_note_mismatch_issues_no_mutation(self, config: GitLabConfig, sleeps: list[float]) -> None:
        transport = FakeGitLabTransport(mr_sha=MOVED_HEAD_SHA)

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            await _approve(_repository(transport, config))

        assert exc_info.value.actual_head_sha == MOVED_HEAD_SHA
        assert {method for method, _path, _body in transport.requests} == {"GET"}

    @pytest.mark.asyncio
    async def test_approve_409_deletes_our_note_exactly_once(self, config: GitLabConfig, sleeps: list[float]) -> None:
        transport = FakeGitLabTransport(approve=409)

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            await _approve(_repository(transport, config))

        assert exc_info.value.error_code is E1011_HEAD_SHA_MISMATCH
        assert exc_info.value.details == {"expected_head_sha": HEAD_SHA, "compliment_note": "deleted"}
        assert len(transport.attempts("POST", "/notes")) == 1
        assert len(transport.attempts("POST", "/approve")) == 1
        assert len(transport.attempts("DELETE", f"/notes/{NOTE_ID}")) == 1
        assert [path for method, path, _ in transport.requests if method == "DELETE"] == [f"{MR_PATH}/notes/{NOTE_ID}"]
        assert sleeps == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("delete_outcome", [502, 429, requests.exceptions.ReadTimeout("slow")])
    async def test_failed_cleanup_is_attempted_once_and_does_not_mask_mismatch(
        self, config: GitLabConfig, sleeps: list[float], delete_outcome: Outcome
    ) -> None:
        transport = FakeGitLabTransport(approve=409, note_delete=delete_outcome)

        with pytest.raises(HeadSHAMismatchError) as exc_info:
            await _approve(_repository(transport, config))

        assert exc_info.value.details == {"expected_head_sha": HEAD_SHA, "compliment_note": "cleanup_failed"}
        assert len(transport.attempts("POST", "/approve")) == 1
        assert len(transport.attempts("DELETE", f"/notes/{NOTE_ID}")) == 1
        assert sleeps == []


class TestLegacyApprovalKeepsSdkRetryPolicy:
    """Control group: proves the harness observes retries when the SHA policy is not applied."""

    @pytest.mark.asyncio
    async def test_no_sha_5xx_still_uses_runtime_retry_defaults(self, config: GitLabConfig, sleeps: list[float]) -> None:
        transport = FakeGitLabTransport(approve=502)

        with pytest.raises(GitLabAPIError):
            await _approve(_repository(transport, config), sha=None)

        assert len(transport.attempts("POST", "/approve")) == 1 + config.max_retries
        assert len(sleeps) == config.max_retries
