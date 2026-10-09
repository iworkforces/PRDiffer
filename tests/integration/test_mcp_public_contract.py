"""Public in-process contracts for the registered FastMCP server."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, assert_never

import pytest
from fastmcp.exceptions import ToolError, ValidationError as FastMCPValidationError
from mcp.types import TextContent

from prdiffer.application.mcp_server import FastMCPServer
from prdiffer.domain.config.mcp_server_config import MCPServerConfig
from prdiffer.application.provider_resolver import create_provider_capability_resolver
from prdiffer.domain.config.github_config import GitHubConfig
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import (
    StrictPRDiffCacheIdentity,
    github_full_diff_v3_identity,
    gitlab_full_diff_v1_identity,
)
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason, HeadSHAMismatchError
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface, LogLevel
from prdiffer.domain.services.settings import SettingsServiceInterface


pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ProviderName = Literal["github", "gitlab"]
OperationName = Literal["get_pr_diff", "approve_pr", "describe_pr"]
ProviderFailure = RuntimeError | FullDiffIncompleteError | HeadSHAMismatchError

GITHUB_URL = "https://github.com/octo-org/widgets/pull/17"
GITLAB_URL = "https://gitlab.com/group/subgroup/project/-/merge_requests/42"
UNSUPPORTED_URL = "https://example.com/team/project/pull/9"
GITHUB_TARGET = ("octo-org", "widgets", 17, None)
GITLAB_TARGET = ("group/subgroup", "project", 42, "https://gitlab.com")


def sample_pr_diff(head_sha: str = "c" * 40) -> PRDiff:
    return PRDiff(
        head_sha=head_sha,
        files=(
            FileDiffResponse(
                path="src/main.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=2, deletions=1),
                diff="@@ full context\n-old\n+new\n context\n",
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class FailurePlan:
    provider: ProviderName
    operation: OperationName
    error: ProviderFailure


class RecordingCache(CacheServiceInterface):
    """Mutable cache fake that records strict reads and writes."""

    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, PRDiff]] = []
        self.entries: dict[tuple[str, str], PRDiff] = {}

    async def get(self, cache_key: str, current_commit_sha: str) -> PRDiff | None:
        self.reads.append((cache_key, current_commit_sha))
        return self.entries.get((cache_key, current_commit_sha))

    async def set(self, cache_key: str, commit_sha: str, data: PRDiff) -> None:
        self.writes.append((cache_key, commit_sha, data))
        self.entries[(cache_key, commit_sha)] = data

    async def invalidate(self, cache_key: str) -> None:
        return None

    async def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
        return None

    async def invalidate_github_repository(self, owner: str, repo: str) -> None:
        return None

    def get_stats(self) -> dict[str, int]:
        return {"size": len(self.writes)}


class RecordingSession:
    """Mutable strict-diff session fake with observable lifecycle counters."""

    def __init__(self, snapshot: PRDiffSnapshot, identity: StrictPRDiffCacheIdentity, result: PRDiff, error: ProviderFailure | None) -> None:
        self.snapshot = snapshot
        self.cache_identity = identity
        self.result = result
        self.error = error
        self.build_calls = 0
        self.close_calls = 0

    async def build_pr_diff(self) -> PRDiff:
        self.build_calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    async def aclose(self) -> None:
        self.close_calls += 1


class RecordingReader:
    """Session-capable reader fake for one provider."""

    def __init__(self, provider: ProviderName, result: PRDiff, error: ProviderFailure | None = None) -> None:
        self.provider: ProviderName = provider
        self.result = result
        self.error = error
        self.open_calls: list[tuple[str, str, int, str | None]] = []
        self.sessions: list[RecordingSession] = []

    async def open_pr_diff_session(
        self,
        owner: str,
        repo: str,
        pr: int,
        /,
        *,
        base_url: str | None = None,
    ) -> RecordingSession:
        self.open_calls.append((owner, repo, pr, base_url))
        match self.provider:
            case "github":
                snapshot = PRDiffSnapshot(owner, repo, pr, "a" * 40, "b" * 40, "c" * 40, len(self.result.files))
                identity = github_full_diff_v3_identity(owner, repo, pr, snapshot.merge_base_sha, snapshot.head_sha)
            case "gitlab":
                snapshot = PRDiffSnapshot(owner, repo, pr, "d" * 40, "e" * 40, "f" * 40, len(self.result.files))
                identity = gitlab_full_diff_v1_identity(
                    owner,
                    repo,
                    pr,
                    7,
                    snapshot.merge_base_sha,
                    snapshot.base_tip_sha,
                    snapshot.head_sha,
                    host="gitlab.com",
                )
            case unreachable:
                assert_never(unreachable)
        session = RecordingSession(snapshot, identity, self.result, self.error)
        self.sessions.append(session)
        return session


class RecordingGitHubRepository(PRDiffRepositoryInterface):
    """Repository fake whose write calls and failures are observable."""

    def __init__(self, owner: str, repo: str, pr: int, failure_operation: OperationName | None, error: ProviderFailure | None) -> None:
        self._repo_owner = owner
        self._repo_name = repo
        self._pr_number = pr
        self.failure_operation = failure_operation
        self.error = error
        self.approve_calls: list[tuple[str, str, str | None]] = []
        self.current_head_sha: str | None = None
        self.describe_calls: list[tuple[str, str]] = []

    @property
    def repo_owner(self) -> str:
        return self._repo_owner

    @property
    def repo_name(self) -> str:
        return self._repo_name

    @property
    def pr_number(self) -> int:
        return self._pr_number

    async def approve_pr_with_comment(self, pr_url: str, compliment: str, *, expected_head_sha: str | None = None) -> str:
        self.approve_calls.append((pr_url, compliment, expected_head_sha))
        if self.failure_operation == "approve_pr" and self.error is not None:
            raise self.error
        if expected_head_sha is not None and self.current_head_sha is not None and expected_head_sha != self.current_head_sha:
            raise HeadSHAMismatchError(expected_head_sha=expected_head_sha, actual_head_sha=self.current_head_sha)
        return "github-approved"

    async def update_pr_description(self, pr_url: str, description: str) -> str:
        self.describe_calls.append((pr_url, description))
        if self.failure_operation == "describe_pr" and self.error is not None:
            raise self.error
        return "github-described"


class RecordingGitHubFactory:
    def __init__(self, failure_operation: OperationName | None = None, error: ProviderFailure | None = None) -> None:
        self.failure_operation: OperationName | None = failure_operation
        self.error = error
        self.calls: list[tuple[str, str, int]] = []
        self.repositories: list[RecordingGitHubRepository] = []
        self.current_head_sha: str | None = None

    def __call__(self, owner: str, repo: str, pr: int) -> RecordingGitHubRepository:
        self.calls.append((owner, repo, pr))
        repository = RecordingGitHubRepository(owner, repo, pr, self.failure_operation, self.error)
        repository.current_head_sha = self.current_head_sha
        self.repositories.append(repository)
        return repository


class RecordingGitLabOperations:
    def __init__(self, failure_operation: OperationName | None = None, error: ProviderFailure | None = None) -> None:
        self.failure_operation = failure_operation
        self.error = error
        self.approve_calls: list[tuple[str, str, int, str, str | None, str | None]] = []
        self.current_head_sha: str | None = None
        self.describe_calls: list[tuple[str, str, int, str, str | None]] = []

    async def approve_pr_with_comment(
        self,
        owner: str,
        repo: str,
        pr: int,
        compliment: str,
        /,
        *,
        base_url: str | None = None,
        expected_head_sha: str | None = None,
    ) -> str:
        self.approve_calls.append((owner, repo, pr, compliment, base_url, expected_head_sha))
        if self.failure_operation == "approve_pr" and self.error is not None:
            raise self.error
        if expected_head_sha is not None and self.current_head_sha is not None and expected_head_sha != self.current_head_sha:
            raise HeadSHAMismatchError(expected_head_sha=expected_head_sha, actual_head_sha=self.current_head_sha)
        return "gitlab-approved"

    async def update_pr_description(
        self,
        owner: str,
        repo: str,
        pr: int,
        description: str,
        /,
        *,
        base_url: str | None = None,
    ) -> str:
        self.describe_calls.append((owner, repo, pr, description, base_url))
        if self.failure_operation == "describe_pr" and self.error is not None:
            raise self.error
        return "gitlab-described"


class RecordingMetrics:
    def __init__(self) -> None:
        self.events: list[tuple[str, bool]] = []

    def track_request(self, operation: str, success: bool, execution_time: float) -> None:
        self.events.append((operation, success))

    def get_metrics_summary(self) -> dict[str, int]:
        return {"requests": len(self.events)}

    def generate_request_id(self) -> str:
        return "contract-request"


class StubSettings(SettingsServiceInterface):
    def get(self, key: str, default: Any = None) -> Any:
        return default

    def get_github_config(self) -> GitHubConfig:
        return GitHubConfig()

    def get_cache_settings(self) -> dict[str, Any]:
        return {}

    def get_app_settings(self) -> dict[str, Any]:
        return {}

    def clear_cache(self) -> None:
        return None


class StubLogger(LoggerServiceInterface):
    def debug(self, message: str, **kwargs: object) -> None:
        return None

    def info(self, message: str, **kwargs: object) -> None:
        return None

    def warning(self, message: str, **kwargs: object) -> None:
        return None

    def error(self, message: str, **kwargs: object) -> None:
        return None

    def critical(self, message: str, **kwargs: object) -> None:
        return None

    def should_log(self, level: LogLevel) -> bool:
        return False


class AllowAllRateLimiter:
    def check_rate_limit(self, identifier: str) -> bool:
        return True

    def increment_rate_limit(self, identifier: str) -> None:
        return None

    def get_rate_limit_info(self) -> dict[str, int]:
        return {"max_requests": 100, "window_seconds": 60}


class AllowAllAuthentication:
    def authenticate(self, api_key: str | None, *, source: str) -> tuple[bool, str]:
        return True, "contract-client"

    def extract_client_identifier(self, headers: dict[str, str]) -> tuple[None, str]:
        return None, "contract-client"

    def is_authentication_enabled(self) -> bool:
        return False

    def get_status(self) -> dict[str, bool]:
        return {"authentication_enabled": False}


class ContractValidator:
    def validate_github_url(self, url: str) -> tuple[str, str, int]:
        assert url == GITHUB_URL
        return GITHUB_TARGET[:3]

    def validate_gitlab_url(self, url: str) -> tuple[str, str, int]:
        assert url == GITLAB_URL
        return GITLAB_TARGET[:3]

    def sanitize_string(self, value: str, max_length: int = 1000) -> str:
        return value

    def sanitize_for_logging(self, value: str, max_length: int = 200) -> str:
        return value[:max_length]


class ImmediateCoalescer:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def coalesce(
        self,
        key: str,
        fetch_func: Callable[[], Awaitable[PRDiff]],
        timeout: float | None = 30.0,
    ) -> PRDiff:
        self.keys.append(key)
        return await fetch_func()

    async def clear(self) -> None:
        self.keys.clear()

    async def get_stats(self) -> dict[str, int | list[str]]:
        return {"pending_count": 0, "pending_keys": [], "total_waiters": 0}


class HealthyMonitor:
    def check_health(self) -> dict[str, str]:
        return {"status": "healthy", "service": "prdiffer"}


class StubServerConfiguration:
    def setup_logging(self) -> None:
        return None

    def get_server_info(self) -> dict[str, str]:
        return {"name": "prdiffer"}

    def get_mcp_instructions(self) -> str:
        return "Contract test server"


class ContractHarness:
    """Compose one isolated real FastMCPServer and its recording providers."""

    def __init__(self, failure: FailurePlan | None = None, *, include_gitlab_capabilities: bool = True) -> None:
        result = sample_pr_diff()
        github_diff_error = failure.error if failure and failure.provider == "github" and failure.operation == "get_pr_diff" else None
        gitlab_diff_error = failure.error if failure and failure.provider == "gitlab" and failure.operation == "get_pr_diff" else None
        github_write_operation = failure.operation if failure and failure.provider == "github" else None
        github_write_error = failure.error if failure and failure.provider == "github" else None
        gitlab_write_operation = failure.operation if failure and failure.provider == "gitlab" else None
        gitlab_write_error = failure.error if failure and failure.provider == "gitlab" else None

        self.github_reader = RecordingReader("github", result, github_diff_error)
        self.gitlab_reader = RecordingReader("gitlab", sample_pr_diff("f" * 40), gitlab_diff_error)
        self.github_factory = RecordingGitHubFactory(github_write_operation, github_write_error)
        self.gitlab_operations = RecordingGitLabOperations(gitlab_write_operation, gitlab_write_error)
        self.cache = RecordingCache()
        self.metrics = RecordingMetrics()
        self.coalescer = ImmediateCoalescer()
        resolver = create_provider_capability_resolver(
            github_reader=self.github_reader,
            github_repository_factory=self.github_factory,
            gitlab_reader=self.gitlab_reader if include_gitlab_capabilities else None,
            gitlab_operations=self.gitlab_operations if include_gitlab_capabilities else None,
        )
        self.server = FastMCPServer(
            mcp_config=MCPServerConfig(transport="http", host="127.0.0.1", port=9102, path="/mcp"),
            settings_service=StubSettings(),
            cache_service=self.cache,
            logger=StubLogger(),
            provider_resolver=resolver,
            rate_limiter=AllowAllRateLimiter(),
            metrics_tracker=self.metrics,
            health_monitor=HealthyMonitor(),
            server_configuration=StubServerConfiguration(),
            authentication=AllowAllAuthentication(),
            input_validator=ContractValidator(),
            request_coalescing_service=self.coalescer,
        )


def call_arguments(operation: OperationName, url: str) -> dict[str, str]:
    match operation:
        case "get_pr_diff":
            return {"pr_url": url}
        case "approve_pr":
            return {"pr_url": url, "compliment": "  excellent work  \n"}
        case "describe_pr":
            return {"pr_url": url, "pr_description": "  updated description  \n"}
        case unreachable:
            assert_never(unreachable)


async def test_registered_tool_discovery_and_schema_contracts() -> None:
    harness = ContractHarness()

    tools = {tool.name: tool for tool in await harness.server.mcp.list_tools()}

    assert set(tools) == {"get_pr_diff", "approve_pr", "describe_pr", "health"}
    expected_required = {
        "get_pr_diff": {"pr_url"},
        "approve_pr": {"pr_url", "compliment"},
        "describe_pr": {"pr_url", "pr_description"},
    }
    for name, required in expected_required.items():
        parameters = tools[name].parameters
        properties = parameters["properties"]
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == required
        assert properties["pr_url"]["type"] == "string"
        assert properties["api_key"]["default"] is None
        assert {choice["type"] for choice in properties["api_key"]["anyOf"]} == {"string", "null"}
    assert tools["approve_pr"].parameters["properties"]["compliment"]["type"] == "string"
    expected_head = tools["approve_pr"].parameters["properties"]["expected_head_sha"]
    assert expected_head["default"] is None
    assert {choice["type"] for choice in expected_head["anyOf"]} == {"string", "null"}
    assert tools["describe_pr"].parameters["properties"]["pr_description"]["type"] == "string"

    health_parameters = tools["health"].parameters
    assert health_parameters["type"] == "object"
    assert health_parameters["properties"] == {}
    assert health_parameters["additionalProperties"] is False

    diff_output = tools["get_pr_diff"].output_schema
    assert diff_output is not None
    assert diff_output["type"] == "object"
    assert diff_output["properties"]["head_sha"]["type"] == "string"
    assert "head_sha" in diff_output["required"]
    files_output = diff_output["properties"]["files"]
    assert files_output["type"] == "array"
    assert set(files_output["items"]["required"]) == {"path", "status", "stats", "diff"}
    assert files_output["items"]["properties"]["previous_path"]["default"] is None
    for name in ("approve_pr", "describe_pr"):
        output = tools[name].output_schema
        assert output is not None
        assert output["type"] == "object"
        assert output["properties"]["result"]["type"] == "string"
        assert "result" in output["required"]
        assert output["x-fastmcp-wrap-result"] is True
    health_output = tools["health"].output_schema
    assert health_output is not None
    assert health_output["type"] == "object"
    assert health_output["additionalProperties"] is True


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("sha_length", [40, 64])
async def test_head_bound_approval_normalizes_sha_public_contract(provider: ProviderName, sha_length: int) -> None:
    harness = ContractHarness()
    url = GITHUB_URL if provider == "github" else GITLAB_URL
    arguments = call_arguments("approve_pr", url) | {"expected_head_sha": "A" * sha_length}

    result = await harness.server.mcp.call_tool("approve_pr", arguments)

    assert result.structured_content == {"result": f"{provider}-approved"}
    if provider == "github":
        assert harness.github_factory.repositories[0].approve_calls == [(url, "excellent work", "a" * sha_length)]
    else:
        assert harness.gitlab_operations.approve_calls == [(*GITLAB_TARGET[:3], "excellent work", GITLAB_TARGET[3], "a" * sha_length)]


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("sha", ["", "g" * 40, "a" * 39, "a" * 41, "a" * 63, "a" * 65])
async def test_malformed_approval_sha_public_contract(provider: ProviderName, sha: str) -> None:
    harness = ContractHarness()
    url = GITHUB_URL if provider == "github" else GITLAB_URL

    with pytest.raises(ToolError, match=r"\[E1001\] expected_head_sha"):
        await harness.server.mcp.call_tool("approve_pr", call_arguments("approve_pr", url) | {"expected_head_sha": sha})

    assert harness.github_factory.calls == []
    assert harness.gitlab_operations.approve_calls == []
    assert harness.metrics.events == [("approve_pr", False)]


@pytest.mark.parametrize("provider", ["github", "gitlab"])
async def test_approval_head_mismatch_public_contract(provider: ProviderName) -> None:
    harness = ContractHarness()
    harness.github_factory.current_head_sha = "b" * 40
    harness.gitlab_operations.current_head_sha = "b" * 40
    url = GITHUB_URL if provider == "github" else GITLAB_URL
    error = HeadSHAMismatchError(expected_head_sha="a" * 40, actual_head_sha="b" * 40)

    with pytest.raises(ToolError) as exc_info:
        await harness.server.mcp.call_tool("approve_pr", call_arguments("approve_pr", url) | {"expected_head_sha": "a" * 40})

    assert json.loads(str(exc_info.value)) == {
        "error_code": "E1011_HEAD_SHA_MISMATCH",
        "message": error.message,
        "details": {"expected_head_sha": "a" * 40, "actual_head_sha": "b" * 40},
    }
    assert harness.metrics.events == [("approve_pr", False)]


async def test_gitlab_approval_mismatch_note_cleanup_public_contract() -> None:
    error = HeadSHAMismatchError(expected_head_sha="a" * 40, compliment_note="deleted")
    harness = ContractHarness(FailurePlan("gitlab", "approve_pr", error))

    with pytest.raises(ToolError) as exc_info:
        await harness.server.mcp.call_tool("approve_pr", call_arguments("approve_pr", GITLAB_URL) | {"expected_head_sha": "a" * 40})

    assert json.loads(str(exc_info.value)) == {
        "error_code": "E1011_HEAD_SHA_MISMATCH",
        "message": error.message,
        "details": {"expected_head_sha": "a" * 40, "compliment_note": "deleted"},
    }


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("empty", [False, True])
async def test_diff_snapshot_head_on_cache_hit_public_contract(provider: ProviderName, empty: bool) -> None:
    harness = ContractHarness()
    reader = harness.github_reader if provider == "github" else harness.gitlab_reader
    url = GITHUB_URL if provider == "github" else GITLAB_URL
    head = ("c" if provider == "github" else "f") * 40
    if empty:
        reader.result = PRDiff(files=(), head_sha=head)
    first = await harness.server.mcp.call_tool("get_pr_diff", {"pr_url": url})

    cached = await harness.server.mcp.call_tool("get_pr_diff", {"pr_url": url})

    assert cached.structured_content == first.structured_content
    assert cached.structured_content is not None
    assert cached.structured_content["head_sha"] == head == reader.sessions[1].snapshot.head_sha
    assert [session.build_calls for session in reader.sessions] == [1, 0]
    assert [session.close_calls for session in reader.sessions] == [1, 1]
    assert len(harness.cache.writes) == 1


async def test_health_call_public_contract() -> None:
    harness = ContractHarness()

    result = await harness.server.mcp.call_tool("health", {})

    assert result.is_error is False
    assert result.structured_content == {
        "status": "healthy",
        "authentication": {"authentication_enabled": False},
        "cache": {},
        "request_coalescing": {"pending_count": 0, "total_waiters": 0},
    }
    assert "repository_cache" not in result.structured_content
    assert result.content
    assert isinstance(result.content[0], TextContent)
    assert "healthy" in result.content[0].text
    assert harness.metrics.events == []


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
async def test_vcs_success_public_contract(provider: ProviderName, operation: OperationName) -> None:
    harness = ContractHarness()
    url = GITHUB_URL if provider == "github" else GITLAB_URL
    expected_target = GITHUB_TARGET if provider == "github" else GITLAB_TARGET

    result = await harness.server.mcp.call_tool(operation, call_arguments(operation, url))

    assert result.is_error is False
    assert result.content
    assert harness.metrics.events == [(operation, True)]
    match operation:
        case "get_pr_diff":
            assert result.structured_content == {
                "head_sha": ("c" if provider == "github" else "f") * 40,
                "files": [
                    {
                        "path": "src/main.py",
                        "status": "modified",
                        "stats": {"additions": 2, "deletions": 1},
                        "diff": "@@ full context\n-old\n+new\n context\n",
                        "previous_path": None,
                    }
                ],
            }
            selected = harness.github_reader if provider == "github" else harness.gitlab_reader
            other = harness.gitlab_reader if provider == "github" else harness.github_reader
            assert selected.open_calls == [expected_target]
            assert other.open_calls == []
            assert len(selected.sessions) == 1
            assert selected.sessions[0].build_calls == 1
            assert selected.sessions[0].close_calls == 1
            assert len(harness.cache.writes) == 1
        case "approve_pr":
            expected_result = "github-approved" if provider == "github" else "gitlab-approved"
            assert result.structured_content == {"result": expected_result}
            if provider == "github":
                assert harness.github_factory.calls == [GITHUB_TARGET[:3]]
                assert harness.github_factory.repositories[0].approve_calls == [(GITHUB_URL, "excellent work", None)]
                assert harness.gitlab_operations.approve_calls == []
            else:
                assert harness.github_factory.calls == []
                assert harness.gitlab_operations.approve_calls == [(*GITLAB_TARGET[:3], "excellent work", GITLAB_TARGET[3], None)]
        case "describe_pr":
            expected_result = "github-described" if provider == "github" else "gitlab-described"
            assert result.structured_content == {"result": expected_result}
            if provider == "github":
                assert harness.github_factory.calls == [GITHUB_TARGET[:3]]
                assert harness.github_factory.repositories[0].describe_calls == [(GITHUB_URL, "updated description")]
                assert harness.gitlab_operations.describe_calls == []
            else:
                assert harness.github_factory.calls == []
                assert harness.gitlab_operations.describe_calls == [(*GITLAB_TARGET[:3], "updated description", GITLAB_TARGET[3])]
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
async def test_unsupported_url_records_operation_correct_failure(operation: OperationName) -> None:
    harness = ContractHarness()

    with pytest.raises(ToolError, match=r"\[E1001\] Invalid request"):
        await harness.server.mcp.call_tool(operation, call_arguments(operation, UNSUPPORTED_URL))

    assert harness.metrics.events == [(operation, False)]
    assert harness.github_reader.open_calls == []
    assert harness.gitlab_reader.open_calls == []
    assert harness.github_factory.calls == []
    assert harness.gitlab_operations.approve_calls == []
    assert harness.gitlab_operations.describe_calls == []


async def test_missing_required_field_raises_fastmcp_validation_error_before_application() -> None:
    harness = ContractHarness()

    with pytest.raises(FastMCPValidationError, match="compliment"):
        await harness.server.mcp.call_tool("approve_pr", {"pr_url": GITHUB_URL})

    assert harness.metrics.events == []
    assert harness.github_factory.calls == []
    assert harness.gitlab_operations.approve_calls == []


@pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
async def test_unavailable_provider_capability_public_contract(operation: OperationName) -> None:
    harness = ContractHarness(include_gitlab_capabilities=False)

    with pytest.raises(ToolError, match="^E5022_PROVIDER_CAPABILITY_UNAVAILABLE$"):
        await harness.server.mcp.call_tool(operation, call_arguments(operation, GITLAB_URL))

    assert harness.metrics.events == [(operation, False)]
    assert harness.github_reader.open_calls == []
    assert harness.gitlab_reader.open_calls == []
    assert harness.github_factory.calls == []
    assert harness.gitlab_operations.approve_calls == []
    assert harness.gitlab_operations.describe_calls == []


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
async def test_provider_runtime_failure_public_contract(provider: ProviderName, operation: OperationName) -> None:
    harness = ContractHarness(FailurePlan(provider, operation, RuntimeError("provider exploded")))
    url = GITHUB_URL if provider == "github" else GITLAB_URL

    with pytest.raises(ToolError) as exc_info:
        await harness.server.mcp.call_tool(operation, call_arguments(operation, url))

    assert "provider exploded" not in str(exc_info.value)
    assert harness.metrics.events == [(operation, False)]
    assert harness.cache.writes == []
    match operation:
        case "get_pr_diff":
            selected = harness.github_reader if provider == "github" else harness.gitlab_reader
            other = harness.gitlab_reader if provider == "github" else harness.github_reader
            assert len(selected.sessions) == 1
            assert selected.sessions[0].build_calls == 1
            assert selected.sessions[0].close_calls == 1
            assert other.open_calls == []
        case "approve_pr":
            if provider == "github":
                assert harness.github_factory.calls == [GITHUB_TARGET[:3]]
                assert harness.github_factory.repositories[0].approve_calls == [(GITHUB_URL, "excellent work", None)]
                assert harness.gitlab_operations.approve_calls == []
            else:
                assert harness.github_factory.calls == []
                assert harness.gitlab_operations.approve_calls == [(*GITLAB_TARGET[:3], "excellent work", GITLAB_TARGET[3], None)]
        case "describe_pr":
            if provider == "github":
                assert harness.github_factory.calls == [GITHUB_TARGET[:3]]
                assert harness.github_factory.repositories[0].describe_calls == [(GITHUB_URL, "updated description")]
                assert harness.gitlab_operations.describe_calls == []
            else:
                assert harness.github_factory.calls == []
                assert harness.gitlab_operations.describe_calls == [(*GITLAB_TARGET[:3], "updated description", GITLAB_TARGET[3])]
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("provider", ["github", "gitlab"])
async def test_e5020_is_structured_nonpartial_uncached_and_closes_session(provider: ProviderName) -> None:
    error = FullDiffIncompleteError(
        FullDiffIncompleteReason.BINARY_CONTENT,
        path="assets/image.bin",
        previous_path="assets/old-image.bin",
        observed=12,
        limit=8,
    )
    harness = ContractHarness(FailurePlan(provider, "get_pr_diff", error))
    url = GITHUB_URL if provider == "github" else GITLAB_URL

    with pytest.raises(ToolError) as exc_info:
        await harness.server.mcp.call_tool("get_pr_diff", {"pr_url": url})

    payload = json.loads(str(exc_info.value))
    assert payload == {
        "error_code": "E5020_FULL_DIFF_INCOMPLETE",
        "message": "Full diff incomplete: BINARY_CONTENT for assets/image.bin",
        "details": {
            "reason": "BINARY_CONTENT",
            "path": "assets/image.bin",
            "previous_path": "assets/old-image.bin",
            "observed": 12,
            "limit": 8,
        },
    }
    assert "files" not in payload
    assert '"files"' not in str(exc_info.value)
    assert harness.cache.writes == []
    assert harness.metrics.events == [("get_pr_diff", False)]
    selected = harness.github_reader if provider == "github" else harness.gitlab_reader
    other = harness.gitlab_reader if provider == "github" else harness.github_reader
    assert len(selected.sessions) == 1
    assert selected.sessions[0].build_calls == 1
    assert selected.sessions[0].close_calls == 1
    assert other.open_calls == []
