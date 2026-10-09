"""Comprehensive tests for ToolRegistry."""

import pytest
import json
from fastmcp import FastMCP
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, assert_never
from unittest.mock import MagicMock, AsyncMock, Mock, patch
from starlette.requests import Request
from prdiffer.application.components.authentication import AuthenticationMiddleware
from urllib.parse import urlparse

from prdiffer.application.tool_registry import ToolRegistry
from prdiffer.application.provider_resolver import (
    ProviderCapabilityResolver,
    ProviderTarget,
    StrictDiffCapability,
    create_provider_capability_resolver,
    parse_github_target,
    parse_gitlab_target,
)
from prdiffer.domain.entities.pr_diff_cache import (
    StrictPRDiffCacheIdentity,
    github_full_diff_v3_identity,
    gitlab_full_diff_v1_identity,
)
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface
from prdiffer.domain.exceptions import (
    InvalidURLError,
    AuthenticationError,
    RateLimitError,
    ValidationError,
    GitHubAPIError,
    HeadSHAMismatchError,
)
from prdiffer.domain.error_codes import E1001_INVALID_URL, E2002_AUTH_FAILED
from fastmcp.exceptions import ToolError
from prdiffer.domain.exceptions import (
    GitLabAPIError,
    ResourceError,
    ProcessingError,
    CacheError,
    ConfigurationError,
    SecurityError,
    InvalidRepositoryError,
    InvalidPRNumberError,
    InputSanitizationError,
    SuspiciousOperationError,
    ProviderCapabilityUnavailableError,
)
import anyio


ProviderName = Literal["github", "gitlab"]


ERROR_CASES = [
    (kind, None, None)
    for kind in (
        AuthenticationError,
        RateLimitError,
        GitHubAPIError,
        GitLabAPIError,
        ValidationError,
        ResourceError,
        ProcessingError,
        CacheError,
        ConfigurationError,
        SecurityError,
        TimeoutError,
    )
] + [
    (InvalidURLError, ValidationError, "Invalid PR or merge request URL"),
    (InvalidRepositoryError, ValidationError, "Invalid repository identifier"),
    (InvalidPRNumberError, ValidationError, "Invalid pull request number"),
    (InputSanitizationError, ValidationError, "Invalid input parameters"),
    (SuspiciousOperationError, ValidationError, "Request contains suspicious patterns"),
    (ValueError, ValidationError, "Invalid input value"),
    (RuntimeError, GitHubAPIError, "Request processing failed"),
    (KeyError, GitHubAPIError, "Missing required field"),
    (AttributeError, GitHubAPIError, "Configuration error"),
    (TypeError, GitHubAPIError, "Invalid input type"),
    (ConnectionError, GitHubAPIError, "Connection to the VCS provider failed"),
    (ProviderCapabilityUnavailableError, ToolError, "E5022_PROVIDER_CAPABILITY_UNAVAILABLE"),
]


@pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
@pytest.mark.parametrize("kind,mapped,safe_message", ERROR_CASES, ids=[case[0].__name__ for case in ERROR_CASES])
async def test_all_exception_outcomes_preserve_client_contract(operation, kind, mapped, safe_message, tool_registry, mock_metrics_tracker):
    # Given: inject at a shared seam inside the original mapping boundary.
    error = kind("distinctive provider failure")
    tool_registry._check_rate_limit = Mock(side_effect=error)
    capture = MCPToolCapture()
    tool_registry.register_tools(capture)
    arguments = {"pr_url": "https://github.com/owner/repo/pull/1"}
    if operation == "approve_pr":
        arguments["compliment"] = "Good work"
    if operation == "describe_pr":
        arguments["pr_description"] = "Description"
    # When / Then: errors retain their previous type, code and exact message.
    with pytest.raises(mapped or kind) as raised:
        await getattr(capture, f"{operation}_tool")(**arguments)
    if mapped is None:
        assert raised.value is error
    elif mapped is ToolError:
        assert str(raised.value) == safe_message
    else:
        prefix = "Invalid request: " if mapped is ValidationError else f"Failed to complete {operation}: "
        assert raised.value.message == prefix + safe_message
        assert str(raised.value.error_code) == ("E1001_INVALID_URL" if mapped is ValidationError else "E5002_GITHUB_API_ERROR")
    mock_metrics_tracker.track_request.assert_called_once()
    assert mock_metrics_tracker.track_request.call_args.args[:2] == (operation, False)


@pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
@pytest.mark.parametrize("gate", ["authentication", "rate_limit"])
async def test_cancelled_call_propagates_without_outcome(operation, gate, tool_registry, mock_metrics_tracker):
    # Given: backend cancellation at either request gate.
    cancellation = anyio.get_cancelled_exc_class()("cancelled")
    if gate == "authentication":
        tool_registry._authenticate_request = AsyncMock(side_effect=cancellation)
    else:
        tool_registry._check_rate_limit = Mock(side_effect=cancellation)
    capture = MCPToolCapture()
    tool_registry.register_tools(capture)
    arguments = {"pr_url": "https://github.com/owner/repo/pull/1"}
    if operation == "approve_pr":
        arguments["compliment"] = "Good work"
    if operation == "describe_pr":
        arguments["pr_description"] = "Description"
    # When / Then.
    with pytest.raises(anyio.get_cancelled_exc_class()) as raised:
        await getattr(capture, f"{operation}_tool")(**arguments)
    assert raised.value is cancellation
    mock_metrics_tracker.track_request.assert_not_called()


@pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
@pytest.mark.parametrize("rejection", ["missing", "false", "lock"])
async def test_auth_rejection_records_one_named_failure_before_provider(
    operation,
    rejection,
    tool_registry,
    mock_authentication,
    mock_metrics_tracker,
    mock_github_repository_class,
    session_reader,
):
    if rejection == "missing":
        tool_registry._authentication = None
    elif rejection == "false":
        mock_authentication.authenticate.return_value = (False, None)
    else:
        mock_authentication.authenticate.side_effect = AuthenticationError("Locked", error_code=E2002_AUTH_FAILED)
    capture = MCPToolCapture()
    tool_registry.register_tools(capture)
    arguments = {"pr_url": "https://github.com/owner/repo/pull/1", "api_key": "bad"}
    if operation == "approve_pr":
        arguments["compliment"] = "Good work"
    if operation == "describe_pr":
        arguments["pr_description"] = "Description"
    tool = getattr(capture, f"{operation}_tool")
    with pytest.raises(AuthenticationError) as raised:
        await tool(**arguments)
    assert raised.value.error_code == E2002_AUTH_FAILED
    mock_metrics_tracker.track_request.assert_called_once()
    assert mock_metrics_tracker.track_request.call_args.args[:2] == (operation, False)
    mock_github_repository_class.assert_not_called()
    assert not session_reader.open_calls


@pytest.mark.parametrize("transport", ["http", "sse", "streamable-http", "stdio"])
def test_server_transport_preserves_actual_peer_identity(monkeypatch, transport):
    from prdiffer.application.mcp_server import FastMCPServer
    from prdiffer.domain.config.mcp_server_config import MCPServerConfig

    server = object.__new__(FastMCPServer)
    server._settings_service = MagicMock()
    server._logger = MagicMock()
    server.mcp = MagicMock()
    server._mcp_config = MCPServerConfig(transport=transport, port=None if transport == "stdio" else 9102, host="127.0.0.1", path="/mcp")
    monkeypatch.setenv("MCP_TRANSPORT", "invalid")
    monkeypatch.setenv("MCP_PORT", "abc")
    server.run()
    if transport == "stdio":
        server.mcp.run.assert_called_once_with(transport="stdio")
    else:
        server.mcp.run.assert_called_once_with(transport=transport, port=9102, host="127.0.0.1", path="/mcp", uvicorn_config={"proxy_headers": False})


@pytest.mark.parametrize("port", [1234, 5678])
async def test_auth_source_uses_http_peer_not_headers_or_port(tool_registry, mock_authentication, port):
    request = Request({"type": "http", "client": ("192.0.2.1", port), "headers": [(b"x-forwarded-for", b"203.0.113.9"), (b"x-real-ip", b"203.0.113.8")]})
    with patch("prdiffer.application.tool_registry.get_http_request", return_value=request):
        assert await tool_registry._authenticate_request("id", 0, "valid", operation="approve_pr") == "client-123"
    mock_authentication.authenticate.assert_called_once_with("valid", source="http:192.0.2.1")


async def test_auth_source_uses_shared_stdio_when_http_context_absent(tool_registry, mock_authentication):
    with patch("prdiffer.application.tool_registry.get_http_request", side_effect=RuntimeError("No active HTTP request found.")):
        await tool_registry._authenticate_request("id", 0, None, operation="get_pr_diff")
    mock_authentication.authenticate.assert_called_once_with(None, source="stdio:local")


@pytest.mark.parametrize("error", ["context broken", "No active HTTP request found"])
async def test_unexpected_transport_runtime_error_propagates(tool_registry, mock_authentication, mock_metrics_tracker, error):
    with patch("prdiffer.application.tool_registry.get_http_request", side_effect=RuntimeError(error)):
        with pytest.raises(RuntimeError, match=error):
            await tool_registry._authenticate_request("id", 0, None, operation="describe_pr")
    mock_authentication.authenticate.assert_not_called()
    mock_metrics_tracker.track_request.assert_not_called()


async def test_unexpected_auth_runtime_error_propagates(tool_registry, mock_authentication, mock_metrics_tracker):
    mock_authentication.authenticate.side_effect = RuntimeError("auth broken")
    with pytest.raises(RuntimeError, match="auth broken"):
        await tool_registry._authenticate_request("id", 0, None, operation="describe_pr")
    mock_metrics_tracker.track_request.assert_not_called()


@pytest.mark.parametrize("peer", [None, ("", 123), ("   ", 123)])
@pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
async def test_http_missing_peer_fails_closed_once(tool_registry, mock_authentication, mock_metrics_tracker, peer, operation):
    request = Request({"type": "http", "client": peer, "headers": []})
    with patch("prdiffer.application.tool_registry.get_http_request", return_value=request):
        with pytest.raises(AuthenticationError) as raised:
            await tool_registry._authenticate_request("id", 0, "valid", operation=operation)
    assert raised.value.error_code == E2002_AUTH_FAILED
    mock_authentication.authenticate.assert_not_called()
    mock_metrics_tracker.track_request.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_real_source_lock_blocks_all_tools_before_all_providers(
    monkeypatch,
    tool_registry,
    mock_metrics_tracker,
    mock_github_repository_class,
    session_reader,
    transport,
):
    monkeypatch.setenv("MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_API_KEYS", "valid_key_12345678901")
    auth = AuthenticationMiddleware(clock=Mock(return_value=1000.0))
    tool_registry._authentication = auth
    gitlab_reader = ProviderReader(PRDiff(files=(), head_sha="f" * 40), "gitlab-head", provider="gitlab")
    gitlab_operations = RecordingGitLabPROps()
    tool_registry._provider_resolver = create_test_provider_resolver(
        session_reader, mock_github_repository_class, gitlab_reader=gitlab_reader, gitlab_operations=gitlab_operations
    )
    capture = MCPToolCapture()
    tool_registry.register_tools(capture)
    peer = Request({"type": "http", "client": ("192.0.2.1", 123), "headers": []})
    with patch("prdiffer.application.tool_registry.get_http_request") as context:
        if transport == "http":
            context.return_value = peer
        else:
            context.side_effect = RuntimeError("No active HTTP request found.")
        for index in range(5):
            with pytest.raises(AuthenticationError):
                await tool_registry._authenticate_request("id", 0, f"wrong_key_{index:020}", operation="get_pr_diff")
        for url in ("https://github.com/owner/repo/pull/1", "https://gitlab.com/group/project/-/merge_requests/2"):
            for operation in ("get_pr_diff", "approve_pr", "describe_pr"):
                mock_metrics_tracker.track_request.reset_mock()
                tool = getattr(capture, f"{operation}_tool")
                arguments = {"pr_url": url, "api_key": "valid_key_12345678901"}
                if operation == "approve_pr":
                    arguments["compliment"] = "Rotated argument"
                if operation == "describe_pr":
                    arguments["pr_description"] = "Another argument"
                peer.scope["client"] = ("192.0.2.1", 456)
                peer.scope["headers"] = [(b"x-forwarded-for", url.encode())]
                with pytest.raises(AuthenticationError) as raised:
                    await tool(**arguments)
                assert raised.value.error_code == E2002_AUTH_FAILED
                mock_metrics_tracker.track_request.assert_called_once()
                assert mock_metrics_tracker.track_request.call_args.args[:2] == (operation, False)
    mock_github_repository_class.assert_not_called()
    assert not session_reader.open_calls and not gitlab_reader.open_calls
    assert not gitlab_operations.approve_calls and not gitlab_operations.describe_calls


@dataclass
class ProviderSession:
    snapshot: PRDiffSnapshot
    cache_identity: StrictPRDiffCacheIdentity
    result: PRDiff
    error: Exception | None = None
    build_calls: int = 0
    close_calls: int = 0

    async def build_pr_diff(self) -> PRDiff:
        self.build_calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    async def aclose(self) -> None:
        self.close_calls += 1


@dataclass
class ProviderReader:
    result: PRDiff
    commit_sha: str
    error: Exception | None = None
    provider: ProviderName = "github"
    open_calls: list[tuple[str, str, int, str | None]] = field(default_factory=list[tuple[str, str, int, str | None]])
    sessions: list[ProviderSession] = field(default_factory=list[ProviderSession])

    async def open_pr_diff_session(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ) -> ProviderSession:
        self.open_calls.append((repo_owner, repo_name, pr_number, base_url))
        match self.provider:
            case "github":
                merge_base_sha = f"{self.commit_sha}-base"
                session = ProviderSession(
                    snapshot=PRDiffSnapshot(repo_owner, repo_name, pr_number, merge_base_sha, merge_base_sha, self.commit_sha, 1),
                    cache_identity=github_full_diff_v3_identity(repo_owner, repo_name, pr_number, merge_base_sha, self.commit_sha),
                    result=self.result,
                    error=self.error,
                )
            case "gitlab":
                base_sha = "dddddddddddddddddddddddddddddddddddddddd"
                start_sha = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
                head_sha = "ffffffffffffffffffffffffffffffffffffffff"
                host = urlparse(base_url).netloc if base_url is not None else "gitlab.com"
                session = ProviderSession(
                    snapshot=PRDiffSnapshot(repo_owner, repo_name, pr_number, start_sha, base_sha, head_sha, 1),
                    cache_identity=gitlab_full_diff_v1_identity(
                        repo_owner,
                        repo_name,
                        pr_number,
                        7,
                        base_sha,
                        start_sha,
                        head_sha,
                        host=host,
                    ),
                    result=self.result,
                    error=self.error,
                )
            case unreachable:
                assert_never(unreachable)
        self.sessions.append(session)
        return session


def create_test_provider_resolver(
    github_reader: ProviderReader,
    github_repository_factory: Callable[[str, str, int], PRDiffRepositoryInterface],
    *,
    gitlab_reader: ProviderReader | None = None,
    gitlab_operations: "RecordingGitLabPROps | None" = None,
) -> ProviderCapabilityResolver:
    return create_provider_capability_resolver(
        github_reader=github_reader,
        github_repository_factory=github_repository_factory,
        gitlab_reader=gitlab_reader,
        gitlab_operations=gitlab_operations,
    )


@dataclass
class RecordingCache:
    lookup_keys: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    write_keys: list[tuple[str, str, PRDiff]] = field(default_factory=list[tuple[str, str, PRDiff]])

    async def get(self, cache_key: str, commit_sha: str) -> None:
        self.lookup_keys.append((cache_key, commit_sha))
        return None

    async def set(self, cache_key: str, commit_sha: str, diff: PRDiff) -> None:
        self.write_keys.append((cache_key, commit_sha, diff))


@dataclass
class RecordingCoalescer:
    keys: list[str] = field(default_factory=list[str])

    async def coalesce(
        self,
        key: str,
        fetch: Callable[[], Awaitable[PRDiff]],
        timeout: float | None = 30.0,
    ) -> PRDiff:
        self.keys.append(key)
        return await fetch()


class ProviderAwareValidator:
    def sanitize_string(self, value: str, max_length: int = 1000) -> str:
        return value

    def sanitize_for_logging(self, value: str, max_length: int = 200) -> str:
        return value

    def validate_github_url(self, url: str) -> tuple[str, str, int]:
        assert url == "https://github.com/owner/repo/pull/17"
        return "owner", "repo", 17

    def validate_gitlab_url(self, url: str) -> tuple[str, str, int]:
        assert url == "https://gitlab.com/owner/repo/-/merge_requests/17"
        return "owner", "repo", 17


class MCPToolCapture:
    def __init__(self) -> None:
        self.get_pr_diff_tool: Callable[..., Awaitable[PRDiff]] | None = None
        self.approve_pr_tool: Callable[..., Awaitable[str]] | None = None
        self.describe_pr_tool: Callable[..., Awaitable[str]] | None = None
        self.registered_names: list[str] = []

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            self.registered_names.append(function.__name__)
            if function.__name__ == "get_pr_diff":
                self.get_pr_diff_tool = function
            elif function.__name__ == "approve_pr":
                self.approve_pr_tool = function
            elif function.__name__ == "describe_pr":
                self.describe_pr_tool = function
            return function

        return decorator


@pytest.fixture
def mock_cache_service():
    """Create mock cache service."""
    mock = MagicMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock()
    return mock


@pytest.fixture
def mock_logger():
    """Create mock logger."""
    mock = MagicMock()
    mock.should_log = MagicMock(return_value=True)
    return mock


@pytest.fixture
def mock_rate_limiter():
    """Create mock rate limiter."""
    mock = MagicMock()
    mock.check_rate_limit = MagicMock(return_value=True)
    mock.increment_rate_limit = MagicMock()
    mock.get_rate_limit_info = MagicMock(
        return_value={
            "max_requests": 100,
            "window_seconds": 60,
        }
    )
    return mock


@pytest.fixture
def mock_metrics_tracker():
    """Create mock metrics tracker."""
    mock = MagicMock()
    mock.generate_request_id = MagicMock(return_value="test-request-id")
    mock.track_request = MagicMock()
    return mock


@pytest.fixture
def mock_authentication():
    """Create mock authentication."""
    mock = MagicMock()
    mock.authenticate = MagicMock(return_value=(True, "client-123"))
    return mock


@pytest.fixture
def mock_input_validator():
    """Create mock input validator."""
    mock = MagicMock()
    mock.sanitize_string = MagicMock(side_effect=lambda x, **kwargs: x)
    mock.sanitize_for_logging = MagicMock(side_effect=lambda x, **kwargs: x)
    mock.validate_github_url = MagicMock(return_value=("owner", "repo", 123))
    return mock


@pytest.fixture
def mock_request_coalescing():
    """Create mock request coalescing service."""
    mock = MagicMock()
    mock.coalesce = AsyncMock(side_effect=lambda key, fn, timeout=None: fn())
    return mock


@pytest.fixture
def mock_github_repository_class():
    """Create mock GitHub repository class."""
    mock_instance = MagicMock()
    mock_instance.approve_pr_with_comment = AsyncMock(return_value="Approved!")
    mock_instance.update_pr_description = AsyncMock(return_value="Description updated!")
    return MagicMock(return_value=mock_instance)


@dataclass
class RecordingGitLabPROps:
    approve_calls: list[tuple[str, str, int, str, str | None, str | None]] = field(default_factory=list)
    approval_error: HeadSHAMismatchError | None = None
    describe_calls: list[tuple[str, str, int, str, str | None]] = field(default_factory=list[tuple[str, str, int, str, str | None]])

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
        if self.approval_error is not None:
            raise self.approval_error
        return f"gitlab-approved:{owner}/{repo}!{pr}"

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
        return f"gitlab-described:{owner}/{repo}!{pr}"


@pytest.fixture
def session_reader() -> ProviderReader:
    return ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head")


@pytest.fixture
def provider_resolver(session_reader: ProviderReader, mock_github_repository_class) -> ProviderCapabilityResolver:
    return create_test_provider_resolver(session_reader, mock_github_repository_class)


@pytest.fixture
def tool_registry(
    mock_cache_service,
    mock_logger,
    mock_github_repository_class,
    mock_rate_limiter,
    mock_metrics_tracker,
    mock_authentication,
    mock_input_validator,
    mock_request_coalescing,
    session_reader,
    provider_resolver,
):
    """Create ToolRegistry with mocked dependencies."""
    return ToolRegistry(
        cache_service=mock_cache_service,
        logger=mock_logger,
        rate_limiter=mock_rate_limiter,
        metrics_tracker=mock_metrics_tracker,
        provider_resolver=provider_resolver,
        authentication=mock_authentication,
        input_validator=mock_input_validator,
        request_coalescing_service=mock_request_coalescing,
    )


@pytest.fixture
def registered_approval_tools(tool_registry, session_reader, mock_github_repository_class):
    operations = RecordingGitLabPROps()
    tool_registry._provider_resolver = create_test_provider_resolver(session_reader, mock_github_repository_class, gitlab_operations=operations)
    tool_registry._input_validator = ProviderAwareValidator()
    mcp = FastMCP("approval-unit-contract")
    tool_registry.register_tools(mcp)
    return mcp, operations


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("sha", [None, "A" * 40, "B" * 64])
async def test_registered_approval_dispatches_normalized_sha(provider, sha, registered_approval_tools, mock_github_repository_class):
    mcp, operations = registered_approval_tools
    url = "https://github.com/owner/repo/pull/17" if provider == "github" else "https://gitlab.com/owner/repo/-/merge_requests/17"
    arguments = {"pr_url": url, "compliment": "Nice work"}
    if sha is not None:
        arguments["expected_head_sha"] = sha

    result = await mcp.call_tool("approve_pr", arguments)

    normalized = sha.lower() if sha is not None else None
    if provider == "github":
        mock_github_repository_class.return_value.approve_pr_with_comment.assert_awaited_once_with(
            pr_url=url, compliment="Nice work", expected_head_sha=normalized
        )
        assert result.structured_content == {"result": "Approved!"}
    else:
        assert operations.approve_calls == [("owner", "repo", 17, "Nice work", "https://gitlab.com", normalized)]
        assert result.structured_content == {"result": "gitlab-approved:owner/repo!17"}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("sha", ["", "z" * 40, "a" * 39])
async def test_registered_approval_rejects_malformed_sha(provider, sha, registered_approval_tools, mock_github_repository_class, mock_metrics_tracker):
    mcp, operations = registered_approval_tools
    url = "https://github.com/owner/repo/pull/17" if provider == "github" else "https://gitlab.com/owner/repo/-/merge_requests/17"

    with pytest.raises(ToolError, match=r"\[E1001\] expected_head_sha"):
        await mcp.call_tool("approve_pr", {"pr_url": url, "compliment": "Nice work", "expected_head_sha": sha})

    mock_github_repository_class.assert_not_called()
    assert operations.approve_calls == []
    mock_metrics_tracker.track_request.assert_called_once_with("approve_pr", False, pytest.approx(0, abs=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "gitlab"])
async def test_registered_approval_mismatch_payload_and_metrics(
    provider, registered_approval_tools, mock_github_repository_class, mock_metrics_tracker, mock_logger
):
    mcp, operations = registered_approval_tools
    error = HeadSHAMismatchError(expected_head_sha="a" * 40, actual_head_sha="b" * 40)
    operations.approval_error = error
    mock_github_repository_class.return_value.approve_pr_with_comment.side_effect = error
    url = "https://github.com/owner/repo/pull/17" if provider == "github" else "https://gitlab.com/owner/repo/-/merge_requests/17"

    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool("approve_pr", {"pr_url": url, "compliment": "Nice work", "expected_head_sha": "a" * 40})

    assert json.loads(str(exc_info.value)) == {
        "error_code": "E1011_HEAD_SHA_MISMATCH",
        "message": error.message,
        "details": {"expected_head_sha": "a" * 40, "actual_head_sha": "b" * 40},
    }
    mock_metrics_tracker.track_request.assert_called_once_with("approve_pr", False, pytest.approx(0, abs=1))
    mock_logger.warning.assert_called_once_with("Approval refused because the head SHA changed", request_id="test-request-id")


@pytest.fixture
def sample_pr_diff():
    """Create sample PRDiff."""
    return PRDiff(
        head_sha="github-head",
        files=(
            FileDiffResponse(
                path="src/test.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=10, deletions=5),
                diff="test diff",
            ),
        ),
    )


class TestToolRegistryInit:
    """Tests for ToolRegistry initialization."""

    def test_init_with_all_dependencies(
        self,
        mock_cache_service,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_input_validator,
        mock_request_coalescing,
        provider_resolver,
    ):
        """Test initialization with all dependencies."""
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=provider_resolver,
            authentication=mock_authentication,
            input_validator=mock_input_validator,
            request_coalescing_service=mock_request_coalescing,
        )

        assert registry._cache_service is mock_cache_service
        assert registry._logger is mock_logger
        assert registry._authentication is mock_authentication
        assert registry._input_validator is mock_input_validator

    def test_generate_request_id(self, tool_registry, mock_metrics_tracker):
        """Test request ID generation."""
        result = tool_registry._generate_request_id()

        mock_metrics_tracker.generate_request_id.assert_called_once()
        assert result == "test-request-id"


class TestCheckRateLimit:
    """Tests for _check_rate_limit method."""

    def test_check_rate_limit_passes(self, tool_registry, mock_rate_limiter):
        """Test rate limit check passes."""
        tool_registry._check_rate_limit("client-123")

        mock_rate_limiter.check_rate_limit.assert_called_once_with("client-123")
        mock_rate_limiter.increment_rate_limit.assert_called_once_with("client-123")

    def test_check_rate_limit_exceeded(self, tool_registry, mock_rate_limiter):
        """Test rate limit exceeded raises error."""
        mock_rate_limiter.check_rate_limit.return_value = False

        with pytest.raises(RateLimitError):
            tool_registry._check_rate_limit("client-123")


class TestCreateSafeErrorMessage:
    """Tests for _create_safe_error_message method."""

    def test_github_exception(self, tool_registry):
        """Test GithubException message."""
        from github import GithubException

        error = GithubException(500, "Internal error", {})

        result = tool_registry._create_safe_error_message(error)

        assert result == "GitHub API error occurred"

    def test_invalid_url_error(self, tool_registry):
        """Test InvalidURLError message."""
        error = InvalidURLError("Bad URL")

        result = tool_registry._create_safe_error_message(error)

        assert result == "Invalid PR or merge request URL"

    def test_connection_error(self, tool_registry):
        """Test ConnectionError message."""
        error = ConnectionError("Connection failed")

        result = tool_registry._create_safe_error_message(error)

        assert result == "Connection to the VCS provider failed"

    def test_timeout_error(self, tool_registry):
        """Test TimeoutError message."""
        error = TimeoutError("Request timed out")

        result = tool_registry._create_safe_error_message(error)

        assert result == "Request timed out"

    def test_value_error(self, tool_registry):
        """Test ValueError message."""
        error = ValueError("Invalid value")

        result = tool_registry._create_safe_error_message(error)

        assert result == "Invalid input value"

    def test_unknown_error(self, tool_registry):
        """Test unknown error message."""
        error = RuntimeError("Unknown error")

        result = tool_registry._create_safe_error_message(error)

        assert result == "Request processing failed"


class TestLogMetricsAndReturnSuccess:
    """Tests for _log_metrics_and_return_success method."""

    def test_log_metrics(self, tool_registry, mock_metrics_tracker, mock_logger, sample_pr_diff):
        """Test logging metrics."""
        start_time = 0.0

        result = tool_registry._log_metrics_and_return_success(start_time, sample_pr_diff)

        mock_metrics_tracker.track_request.assert_not_called()
        mock_logger.info.assert_called()
        assert result is sample_pr_diff


class TestHandleSecurityException:
    """Tests for _handle_security_exception method."""

    def test_handle_security_exception(self, tool_registry, mock_metrics_tracker, mock_logger):
        """Test handling security exception."""
        error = InvalidURLError("Invalid URL")

        with pytest.raises(ValidationError):
            tool_registry._handle_security_exception(error, 0.0, "req-123", "https://github.com/owner/repo/pull/123")

        mock_metrics_tracker.track_request.assert_not_called()
        mock_logger.warning.assert_called()


class TestHandleValidationException:
    """Tests for _handle_validation_exception method."""

    def test_handle_validation_exception(self, tool_registry, mock_metrics_tracker, mock_logger):
        """Test handling validation exception."""
        error = ValueError("Invalid value")

        with pytest.raises(ValidationError):
            tool_registry._handle_validation_exception(error, 0.0, "req-123", "https://github.com/owner/repo/pull/123")

        mock_metrics_tracker.track_request.assert_not_called()
        mock_logger.warning.assert_called()


class TestHandleRuntimeException:
    """Tests for _handle_runtime_exception method."""

    def test_handle_runtime_exception(self, tool_registry, mock_metrics_tracker, mock_logger):
        """Test handling runtime exception."""
        error = RuntimeError("Runtime error")

        with pytest.raises(GitHubAPIError):
            tool_registry._handle_runtime_exception(error, 0.0, "req-123", "https://github.com/owner/repo/pull/123")

        mock_metrics_tracker.track_request.assert_not_called()
        mock_logger.error.assert_called()


class TestAuthenticateRequest:
    """Tests for _authenticate_request method."""

    @pytest.mark.anyio
    async def test_authenticate_success(self, tool_registry, mock_authentication):
        """Test successful authentication."""
        result = await tool_registry._authenticate_request("req-123", 0.0, "api-key-123", operation="get_pr_diff")

        mock_authentication.authenticate.assert_called_once_with("api-key-123", source="stdio:local")
        assert result == "client-123"

    @pytest.mark.anyio
    async def test_authenticate_no_service(self, tool_registry):
        """Test authentication with no service."""
        tool_registry._authentication = None

        with pytest.raises(AuthenticationError):
            await tool_registry._authenticate_request("req-123", 0.0, "api-key-123", operation="get_pr_diff")

    @pytest.mark.anyio
    async def test_authenticate_failed(self, tool_registry, mock_authentication):
        """Test failed authentication."""
        mock_authentication.authenticate.return_value = (False, None)

        with pytest.raises(AuthenticationError):
            await tool_registry._authenticate_request("req-123", 0.0, "api-key-123", operation="get_pr_diff")

    @pytest.mark.anyio
    @pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
    async def test_authenticate_lock_records_calling_tool(self, tool_registry, mock_authentication, mock_metrics_tracker, operation):
        """Rate-limited authentication failures are attributed to the calling tool."""
        mock_authentication.authenticate.side_effect = AuthenticationError("Locked", error_code=E2002_AUTH_FAILED)

        with pytest.raises(AuthenticationError):
            await tool_registry._authenticate_request("req-123", 0.0, "api-key-123", operation=operation)

        mock_metrics_tracker.track_request.assert_not_called()


class TestExecuteUseCaseWithCoalescing:
    """Tests for _execute_use_case_with_coalescing method."""

    @pytest.mark.anyio
    async def test_execute_success(self, tool_registry, mock_request_coalescing, session_reader, sample_pr_diff):
        """Test successful use case execution."""
        mock_request_coalescing.coalesce = AsyncMock(return_value=sample_pr_diff)

        with patch("prdiffer.application.pr_diff_executor.GetPRDiffUseCase") as MockUseCase:
            mock_use_case = MagicMock()
            mock_use_case.execute = AsyncMock(return_value=sample_pr_diff)
            MockUseCase.return_value = mock_use_case

            result = await tool_registry._execute_use_case_with_coalescing("owner", "repo", 123, pr_diff_reader=session_reader)

            assert result is sample_pr_diff


class TestRegisterTools:
    """Tests for register_tools method."""

    def test_register_tools(self, tool_registry):
        """Test tool registration."""
        mock_mcp = MagicMock()

        # Mock the decorator
        decorated_tools = []

        def mock_tool_decorator():
            def decorator(func):
                decorated_tools.append(func.__name__)
                return func

            return decorator

        mock_mcp.tool = mock_tool_decorator

        tool_registry.register_tools(mock_mcp)

        assert "get_pr_diff" in decorated_tools
        assert "approve_pr" in decorated_tools
        assert "describe_pr" in decorated_tools


class TestSafeErrorMessages:
    """Tests for all error message mappings."""

    def test_key_error_message(self, tool_registry):
        """Test KeyError message."""
        error = KeyError("missing_key")

        result = tool_registry._create_safe_error_message(error)

        assert result == "Missing required field"

    def test_attribute_error_message(self, tool_registry):
        """Test AttributeError message."""
        error = AttributeError("missing_attribute")

        result = tool_registry._create_safe_error_message(error)

        assert result == "Configuration error"

    def test_type_error_message(self, tool_registry):
        """Test TypeError message."""
        error = TypeError("wrong type")

        result = tool_registry._create_safe_error_message(error)

        assert result == "Invalid input type"


class TestGetPRDiffProviderDispatch:
    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("url", "provider", "coalesce_key"),
        [
            ("https://github.com/owner/repo/pull/17", "github", "owner/repo/pr/17"),
            (
                "https://gitlab.com/owner/repo/-/merge_requests/17",
                "gitlab",
                "https://gitlab.com:gitlab:owner/repo/pr/17",
            ),
        ],
    )
    async def test_registered_get_pr_diff_routes_to_only_the_matching_provider_reader(
        self,
        url: str,
        provider: ProviderName,
        coalesce_key: str,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
    ) -> None:
        # Given
        github_diff = PRDiff(head_sha="github-head", files=(FileDiffResponse("github.py", EDIT_TYPE.MODIFIED, FileStats(additions=1, deletions=0), "+github"),))
        gitlab_diff = PRDiff(head_sha="f" * 40, files=(FileDiffResponse("gitlab.py", EDIT_TYPE.ADDED, FileStats(additions=1, deletions=0), "+gitlab"),))
        github_reader = ProviderReader(github_diff, "github-commit", provider="github")
        gitlab_reader = ProviderReader(gitlab_diff, "f" * 40, provider="gitlab")
        cache = RecordingCache()
        coalescer = RecordingCoalescer()
        resolver = create_test_provider_resolver(
            github_reader,
            mock_github_repository_class,
            gitlab_reader=gitlab_reader,
        )
        registry = ToolRegistry(
            cache_service=cache,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=resolver,
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=coalescer,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        get_pr_diff = mcp.get_pr_diff_tool
        assert get_pr_diff is not None
        readers = {"github": github_reader, "gitlab": gitlab_reader}
        expected_diff = {"github": github_diff, "gitlab": gitlab_diff}[provider]
        selected_reader = readers[provider]
        other_reader = readers[{"github": "gitlab", "gitlab": "github"}[provider]]

        # When
        result = await get_pr_diff(url, None)

        # Then
        assert result is expected_diff
        assert selected_reader.open_calls == [
            ("owner", "repo", 17, "https://gitlab.com" if provider == "gitlab" else None),
        ]
        assert selected_reader.sessions[0].build_calls == 1
        assert selected_reader.sessions[0].close_calls == 1
        assert other_reader.open_calls == []
        session_identity = selected_reader.sessions[0].cache_identity
        match provider:
            case "github":
                assert session_identity.cache_key == "github-full-diff-v3:owner:repo:17:github-commit-base:github-commit"
                assert session_identity.validation_token == "github-commit-base:github-commit"
                assert session_identity.schema_version == 2
            case "gitlab":
                assert session_identity.cache_key == (
                    "gitlab-full-diff-v1:gitlab.com:owner:repo:17:7:"
                    "dddddddddddddddddddddddddddddddddddddddd:"
                    "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee:"
                    "ffffffffffffffffffffffffffffffffffffffff"
                )
                assert session_identity.validation_token == (
                    "7:dddddddddddddddddddddddddddddddddddddddd:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee:ffffffffffffffffffffffffffffffffffffffff"
                )
                assert session_identity.schema_version == 1
            case unreachable:
                assert_never(unreachable)
        assert cache.lookup_keys == [(session_identity.cache_key, session_identity.validation_token)]
        assert cache.write_keys == [(session_identity.cache_key, session_identity.validation_token, expected_diff)]
        assert coalescer.keys == [coalesce_key]

    @pytest.mark.anyio
    async def test_gitlab_reader_uses_base_url_netloc_for_cache_host(self) -> None:
        # Given
        reader = ProviderReader(PRDiff(files=(), head_sha="f" * 40), "f" * 40, provider="gitlab")

        # When
        session = await reader.open_pr_diff_session(
            "owner",
            "repo",
            17,
            base_url="https://gitlab.example.com:8443",
        )

        # Then
        try:
            assert session.cache_identity.cache_key.startswith("gitlab-full-diff-v1:gitlab.example.com:8443:owner:repo:17:7:")
        finally:
            await session.aclose()


@pytest.mark.unit
@pytest.mark.asyncio
class TestFullDiffIncompleteToolError:
    async def test_full_diff_incomplete_raises_structured_tool_error(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
    ) -> None:
        import json
        from fastmcp.exceptions import ToolError
        from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason

        class BoomReader(ProviderReader):
            def __init__(self) -> None:
                super().__init__(
                    PRDiff(files=(), head_sha="f" * 40),
                    "boom-head",
                    error=FullDiffIncompleteError(
                        FullDiffIncompleteReason.BINARY_CONTENT,
                        path="bin.dat",
                        previous_path="old.bin",
                        observed=10,
                        limit=5,
                    ),
                )

        class PassthroughCoalescer:
            async def coalesce(self, key, fn, timeout=None):
                return await fn()

            def clear(self) -> None:
                return None

            def get_stats(self) -> dict:
                return {}

        reader = BoomReader()
        registry = ToolRegistry(
            cache_service=RecordingCache(),
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(reader, mock_github_repository_class),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=PassthroughCoalescer(),
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        get_pr_diff = mcp.get_pr_diff_tool
        assert get_pr_diff is not None

        with pytest.raises(ToolError) as exc_info:
            await get_pr_diff("https://github.com/owner/repo/pull/17", None)

        payload = json.loads(str(exc_info.value))
        assert list(payload.keys()) == ["error_code", "message", "details"]
        assert payload["error_code"] == "E5020_FULL_DIFF_INCOMPLETE"
        assert payload["details"]["reason"] == "BINARY_CONTENT"
        assert payload["details"]["path"] == "bin.dat"
        assert payload["details"]["previous_path"] == "old.bin"
        assert payload["details"]["observed"] == 10
        assert payload["details"]["limit"] == 5
        assert "files" not in payload
        assert "token" not in json.dumps(payload)
        mock_metrics_tracker.track_request.assert_called()
        # Exactly one failure metric for this tool invocation
        fail_calls = [c for c in mock_metrics_tracker.track_request.call_args_list if c.args[:2] == ("get_pr_diff", False)]
        assert len(fail_calls) == 1
        assert reader.sessions[0].close_calls == 1

    async def test_non_e5020_errors_not_remapped_to_tool_error_json(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
    ) -> None:
        from prdiffer.domain.exceptions import AuthenticationError
        from prdiffer.domain.error_codes import E2006_GITLAB_AUTH_FAILED

        class AuthBoom(ProviderReader):
            def __init__(self) -> None:
                super().__init__(
                    PRDiff(files=(), head_sha="f" * 40),
                    "auth-head",
                    error=AuthenticationError("nope", error_code=E2006_GITLAB_AUTH_FAILED),
                )

        class PassthroughCoalescer:
            async def coalesce(self, key, fn, timeout=None):
                return await fn()

            def clear(self) -> None:
                return None

            def get_stats(self) -> dict:
                return {}

        reader = AuthBoom()
        registry = ToolRegistry(
            cache_service=RecordingCache(),
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(reader, mock_github_repository_class),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=PassthroughCoalescer(),
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        get_pr_diff = mcp.get_pr_diff_tool
        assert get_pr_diff is not None

        with pytest.raises(AuthenticationError) as exc_info:
            await get_pr_diff("https://github.com/owner/repo/pull/17", None)
        assert exc_info.value.error_code is E2006_GITLAB_AUTH_FAILED
        assert reader.sessions[0].close_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestApproveDescribeProviderDispatch:
    async def test_approve_pr_routes_github_to_github_repository(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_input_validator,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        mock_input_validator.validate_github_url = MagicMock(return_value=("owner", "repo", 17))
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
                gitlab_operations=RecordingGitLabPROps(),
            ),
            authentication=mock_authentication,
            input_validator=mock_input_validator,
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.approve_pr_tool is not None

        result = await mcp.approve_pr_tool("https://github.com/owner/repo/pull/17", "Nice work", None)

        assert result == "Approved!"
        mock_github_repository_class.assert_called_once_with("owner", "repo", 17)
        instance = mock_github_repository_class.return_value
        instance.approve_pr_with_comment.assert_awaited_once()
        success_calls = [call for call in mock_metrics_tracker.track_request.call_args_list if call.args[:2] == ("approve_pr", True)]
        assert len(success_calls) == 1

    async def test_approve_pr_routes_gitlab_to_gitlab_operations(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        gitlab_ops = RecordingGitLabPROps()
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
                gitlab_operations=gitlab_ops,
            ),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.approve_pr_tool is not None

        result = await mcp.approve_pr_tool(
            "https://gitlab.com/owner/repo/-/merge_requests/17",
            "Great MR",
            None,
        )

        assert result == "gitlab-approved:owner/repo!17"
        assert gitlab_ops.approve_calls == [
            ("owner", "repo", 17, "Great MR", "https://gitlab.com", None),
        ]
        mock_github_repository_class.assert_not_called()

    async def test_approve_pr_rejects_empty_compliment_for_gitlab(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        gitlab_ops = RecordingGitLabPROps()
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
                gitlab_operations=gitlab_ops,
            ),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.approve_pr_tool is not None

        with pytest.raises(ValidationError) as exc_info:
            await mcp.approve_pr_tool(
                "https://gitlab.com/owner/repo/-/merge_requests/17",
                "",
                None,
            )

        assert exc_info.value.error_code is E1001_INVALID_URL
        assert gitlab_ops.approve_calls == []

    async def test_describe_pr_routes_gitlab_to_gitlab_operations(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        gitlab_ops = RecordingGitLabPROps()
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
                gitlab_operations=gitlab_ops,
            ),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.describe_pr_tool is not None

        result = await mcp.describe_pr_tool(
            "https://gitlab.com/owner/repo/-/merge_requests/17",
            "Updated body",
            None,
        )

        assert result == "gitlab-described:owner/repo!17"
        assert gitlab_ops.describe_calls == [
            ("owner", "repo", 17, "Updated body", "https://gitlab.com"),
        ]
        mock_github_repository_class.assert_not_called()

    async def test_describe_pr_rejects_empty_description_for_gitlab(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        gitlab_ops = RecordingGitLabPROps()
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
                gitlab_operations=gitlab_ops,
            ),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.describe_pr_tool is not None

        with pytest.raises(ValidationError) as exc_info:
            await mcp.describe_pr_tool(
                "https://gitlab.com/owner/repo/-/merge_requests/17",
                "",
                None,
            )

        assert exc_info.value.error_code is E1001_INVALID_URL
        assert gitlab_ops.describe_calls == []

    async def test_describe_pr_routes_github(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_input_validator,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        mock_input_validator.validate_github_url = MagicMock(return_value=("owner", "repo", 17))
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
            ),
            authentication=mock_authentication,
            input_validator=mock_input_validator,
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.describe_pr_tool is not None

        result = await mcp.describe_pr_tool(
            "https://github.com/owner/repo/pull/17",
            "New description",
            None,
        )

        assert result == "Description updated!"
        mock_github_repository_class.assert_called_once_with("owner", "repo", 17)
        instance = mock_github_repository_class.return_value
        instance.update_pr_description.assert_awaited_once()

    async def test_approve_pr_rejects_whitespace_only_compliment(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        gitlab_ops = RecordingGitLabPROps()
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
                gitlab_operations=gitlab_ops,
            ),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.approve_pr_tool is not None

        with pytest.raises(ValidationError):
            await mcp.approve_pr_tool(
                "https://gitlab.com/owner/repo/-/merge_requests/17",
                "   \n",
                None,
            )
        assert gitlab_ops.approve_calls == []

    async def test_approve_pr_raises_when_gitlab_ops_not_configured(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
            ),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.approve_pr_tool is not None

        with pytest.raises(ToolError) as exc_info:
            await mcp.approve_pr_tool(
                "https://gitlab.com/owner/repo/-/merge_requests/17",
                "Nice",
                None,
            )

        assert str(exc_info.value) == "E5022_PROVIDER_CAPABILITY_UNAVAILABLE"
        fail_metrics = [c for c in mock_metrics_tracker.track_request.call_args_list if c.args[:2] == ("approve_pr", False)]
        assert len(fail_metrics) == 1

    async def test_approve_pr_forwards_nested_namespace_and_strips_compliment(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
        mock_request_coalescing,
        mock_cache_service,
    ) -> None:
        class NestedValidator(ProviderAwareValidator):
            def validate_gitlab_url(self, url: str) -> tuple[str, str, int]:
                assert "group/sub/project" in url
                return "group/sub", "project", 3

        gitlab_ops = RecordingGitLabPROps()
        registry = ToolRegistry(
            cache_service=mock_cache_service,
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(
                ProviderReader(PRDiff(files=(), head_sha="github-head"), "github-head"),
                mock_github_repository_class,
                gitlab_operations=gitlab_ops,
            ),
            authentication=mock_authentication,
            input_validator=NestedValidator(),
            request_coalescing_service=mock_request_coalescing,
        )
        mcp = MCPToolCapture()
        registry.register_tools(mcp)
        assert mcp.approve_pr_tool is not None

        # parse_pr_target also calls parse_gitlab_merge_request_parts on real URL
        with patch("prdiffer.application.utils.pr_url_parser.parse_gitlab_merge_request_parts") as mock_parts:
            mock_parts.return_value = MagicMock(
                namespace="group/sub",
                project="project",
                iid=3,
                base_url="https://gitlab.com",
            )
            result = await mcp.approve_pr_tool(
                "https://gitlab.com/group/sub/project/-/merge_requests/3",
                "  Solid nested MR  ",
                None,
            )

        assert result == "gitlab-approved:group/sub/project!3"
        assert gitlab_ops.approve_calls == [
            ("group/sub", "project", 3, "Solid nested MR", "https://gitlab.com", None),
        ]


@pytest.mark.unit
@pytest.mark.asyncio
class TestProviderCapabilityResolver:
    async def test_strict_diff_capability_rejects_reader_without_session_protocol(self) -> None:
        class NonSessionReader:
            async def get_pr_diff(self, repo_owner: str, repo_name: str, pr_number: int, /) -> PRDiff:
                return PRDiff(files=(), head_sha="c" * 40)

            async def get_latest_commit_sha(self, repo_owner: str, repo_name: str, pr_number: int, /) -> str:
                return "head"

        with pytest.raises(TypeError, match="session-capable reader"):
            StrictDiffCapability(NonSessionReader(), "other")

    async def test_third_provider_registration_routes_all_advertised_capabilities(
        self,
        mock_logger,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
    ) -> None:
        class ThirdProviderWrites:
            async def approve(self, target: ProviderTarget, compliment: str, /, *, expected_head_sha: str | None = None) -> str:
                return f"approved:{target.repo_owner}/{target.repo_name}:{compliment}"

            async def describe(self, target: ProviderTarget, description: str, /) -> str:
                return f"described:{target.repo_owner}/{target.repo_name}:{description}"

        third_diff = PRDiff(files=(), head_sha="third-head")
        reader = ProviderReader(third_diff, "third-head")
        resolver = ProviderCapabilityResolver()

        def parse_third(url: str, _validator: ProviderAwareValidator) -> ProviderTarget | None:
            if url.startswith("https://third.example/"):
                return ProviderTarget("third", "team/sub", "project", 9, url, "https://third.example")
            return None

        resolver.register_parser("third", parse_third)
        resolver.register_strict_diff("third", StrictDiffCapability(reader, "third"))
        writes = ThirdProviderWrites()
        resolver.register_approval("third", writes)
        resolver.register_description("third", writes)
        registry = ToolRegistry(
            cache_service=RecordingCache(),
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=RecordingCoalescer(),
            provider_resolver=resolver,
        )
        capture = MCPToolCapture()
        registry.register_tools(capture)

        assert capture.get_pr_diff_tool is not None
        assert capture.approve_pr_tool is not None
        assert capture.describe_pr_tool is not None
        assert await capture.get_pr_diff_tool("https://third.example/team/sub/project/changes/9") == third_diff
        assert await capture.approve_pr_tool("https://third.example/team/sub/project/changes/9", "Great", None) == "approved:team/sub/project:Great"
        assert await capture.describe_pr_tool("https://third.example/team/sub/project/changes/9", "Body", None) == "described:team/sub/project:Body"

    @pytest.mark.parametrize("operation", ["get_pr_diff", "approve_pr", "describe_pr"])
    async def test_missing_capability_returns_e5022_before_provider_invocation(
        self,
        operation: str,
        mock_logger,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
    ) -> None:
        resolver = ProviderCapabilityResolver()

        def parse_read_only(url: str, _validator: ProviderAwareValidator) -> ProviderTarget | None:
            if url.startswith("https://readonly.example/"):
                return ProviderTarget("read-only", "team", "project", 9, url)
            return None

        resolver.register_parser("read-only", parse_read_only)
        registry = ToolRegistry(
            cache_service=RecordingCache(),
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=RecordingCoalescer(),
            provider_resolver=resolver,
        )
        capture = MCPToolCapture()
        registry.register_tools(capture)

        with pytest.raises(ToolError, match="E5022_PROVIDER_CAPABILITY_UNAVAILABLE"):
            if operation == "get_pr_diff":
                assert capture.get_pr_diff_tool is not None
                await capture.get_pr_diff_tool("https://readonly.example/team/project/9")
            elif operation == "approve_pr":
                assert capture.approve_pr_tool is not None
                await capture.approve_pr_tool("https://readonly.example/team/project/9", "Great", None)
            else:
                assert capture.describe_pr_tool is not None
                await capture.describe_pr_tool("https://readonly.example/team/project/9", "Body", None)

        failed_calls = [call for call in mock_metrics_tracker.track_request.call_args_list if call.args[:2] == (operation, False)]
        assert len(failed_calls) == 1

    @pytest.mark.parametrize(
        ("padded_url", "provider", "expected_url", "expected_base_url"),
        [
            (
                "  https://github.com/owner/repo/pull/17\n",
                "github",
                "https://github.com/owner/repo/pull/17",
                None,
            ),
            (
                "\thttps://gitlab.com/owner/repo/-/merge_requests/17  ",
                "gitlab",
                "https://gitlab.com/owner/repo/-/merge_requests/17",
                "https://gitlab.com",
            ),
        ],
    )
    async def test_resolve_target_strips_surrounding_whitespace_before_prefix_match(
        self,
        padded_url: str,
        provider: ProviderName,
        expected_url: str,
        expected_base_url: str | None,
        session_reader: ProviderReader,
        mock_github_repository_class,
    ) -> None:
        resolver = create_test_provider_resolver(
            session_reader,
            mock_github_repository_class,
            gitlab_reader=ProviderReader(PRDiff(files=(), head_sha="f" * 40), "f" * 40, provider="gitlab"),
        )

        target = resolver.resolve_target(padded_url, ProviderAwareValidator())

        assert target.provider == provider
        assert target.repo_owner == "owner"
        assert target.repo_name == "repo"
        assert target.pr_number == 17
        assert target.url == expected_url
        assert target.base_url == expected_base_url

    async def test_default_parsers_strip_before_ownership_prefix_checks(self) -> None:
        validator = ProviderAwareValidator()

        github = parse_github_target(" \nhttps://github.com/owner/repo/pull/17 ", validator)
        gitlab = parse_gitlab_target("\thttps://gitlab.com/owner/repo/-/merge_requests/17\n", validator)

        assert github is not None
        assert github.provider == "github"
        assert github.url == "https://github.com/owner/repo/pull/17"
        assert gitlab is not None
        assert gitlab.provider == "gitlab"
        assert gitlab.url == "https://gitlab.com/owner/repo/-/merge_requests/17"
        assert gitlab.base_url == "https://gitlab.com"

    async def test_resolve_target_rejects_whitespace_only_url(
        self,
        session_reader: ProviderReader,
        mock_github_repository_class,
    ) -> None:
        resolver = create_test_provider_resolver(session_reader, mock_github_repository_class)

        with pytest.raises(InvalidURLError, match="empty or whitespace-only"):
            resolver.resolve_target(" \t\n ", ProviderAwareValidator())

    @pytest.mark.anyio
    async def test_get_pr_diff_accepts_leading_whitespace_github_url(
        self,
        mock_logger,
        mock_github_repository_class,
        mock_rate_limiter,
        mock_metrics_tracker,
        mock_authentication,
    ) -> None:
        github_diff = PRDiff(files=(), head_sha="github-head")
        github_reader = ProviderReader(github_diff, "github-commit", provider="github")
        registry = ToolRegistry(
            cache_service=RecordingCache(),
            logger=mock_logger,
            rate_limiter=mock_rate_limiter,
            metrics_tracker=mock_metrics_tracker,
            provider_resolver=create_test_provider_resolver(github_reader, mock_github_repository_class),
            authentication=mock_authentication,
            input_validator=ProviderAwareValidator(),
            request_coalescing_service=RecordingCoalescer(),
        )
        capture = MCPToolCapture()
        registry.register_tools(capture)
        assert capture.get_pr_diff_tool is not None

        result = await capture.get_pr_diff_tool("  https://github.com/owner/repo/pull/17", None)

        assert result is github_diff
        assert github_reader.open_calls == [("owner", "repo", 17, None)]
