"""In-process FastMCP surface tests for strict full-diff responses."""

from __future__ import annotations

from typing import Literal, assert_never
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from fastmcp import FastMCP

from prdiffer.application.tool_registry import ToolRegistry
from prdiffer.application.provider_resolver import StrictDiffCapability, create_provider_capability_resolver
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import (
    StrictPRDiffCacheIdentity,
    github_full_diff_v3_identity,
    gitlab_full_diff_v1_identity,
)
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffReadSessionInterface, PRDiffSnapshot
from prdiffer.domain.services.pr_diff_service import PRDiffServiceInterface
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason


ProviderName = Literal["github", "gitlab"]


def _mixed_pr_diff() -> PRDiff:
    return PRDiff(
        files=(
            FileDiffResponse(
                path="mod.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=1, deletions=1),
                diff="@@ full\n context\n-old\n+new\n",
            ),
            FileDiffResponse(
                path="gone.py",
                status=EDIT_TYPE.DELETED,
                stats=FileStats(additions=0, deletions=1),
                diff="@@\n-line\n",
            ),
            FileDiffResponse(
                path="new_name.py",
                status=EDIT_TYPE.RENAMED,
                stats=FileStats(additions=0, deletions=0),
                diff="rename from old_name.py\nrename to new_name.py\n",
                previous_path="old_name.py",
            ),
        )
    )


class RecordingCache:
    def __init__(self) -> None:
        self.sets = 0
        self.store: dict[tuple[str, str], object] = {}

    def get_cache_key(self, owner: str, repo: str, pr: int) -> str:
        return f"{owner}/{repo}/{pr}"

    async def get_optimistic(self, key: str):
        return None, None

    async def get(self, key: str, token: str):
        return self.store.get((key, token))

    async def set(self, key: str, token: str, value: object) -> None:
        self.sets += 1
        self.store[(key, token)] = value


class FakeSession(PRDiffReadSessionInterface):
    def __init__(
        self,
        pr_diff: PRDiff,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        provider: ProviderName,
        base_url: str | None,
    ) -> None:
        self._pr_diff = pr_diff
        match provider:
            case "github":
                self._snapshot = PRDiffSnapshot(
                    repo_owner,
                    repo_name,
                    pr_number,
                    "a" * 40,
                    "b" * 40,
                    "c" * 40,
                    len(pr_diff.files),
                )
                self._cache_identity = github_full_diff_v3_identity(
                    repo_owner,
                    repo_name,
                    pr_number,
                    self._snapshot.merge_base_sha,
                    self._snapshot.head_sha,
                )
            case "gitlab":
                base_sha = "dddddddddddddddddddddddddddddddddddddddd"
                start_sha = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
                head_sha = "ffffffffffffffffffffffffffffffffffffffff"
                host = urlparse(base_url).netloc if base_url is not None else "gitlab.com"
                self._snapshot = PRDiffSnapshot(
                    repo_owner,
                    repo_name,
                    pr_number,
                    start_sha,
                    base_sha,
                    head_sha,
                    len(pr_diff.files),
                )
                self._cache_identity = gitlab_full_diff_v1_identity(
                    repo_owner,
                    repo_name,
                    pr_number,
                    7,
                    base_sha,
                    start_sha,
                    head_sha,
                    host=host,
                )
            case unreachable:
                assert_never(unreachable)

    @property
    def snapshot(self) -> PRDiffSnapshot:
        return self._snapshot

    @property
    def cache_identity(self) -> StrictPRDiffCacheIdentity:
        return self._cache_identity

    async def build_pr_diff(self) -> PRDiff:
        return self._pr_diff

    async def aclose(self) -> None:
        return None


class FakeReader(PRDiffServiceInterface):
    def __init__(self, pr_diff: PRDiff | None = None, *, provider: ProviderName = "github") -> None:
        self._pr_diff = pr_diff or PRDiff(files=())
        self._provider: ProviderName = provider

    async def open_pr_diff_session(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ) -> PRDiffReadSessionInterface:
        return FakeSession(self._pr_diff, repo_owner, repo_name, pr_number, self._provider, base_url)

    async def get_pr_diff(self, repo_owner: str, repo_name: str, pr_number: int) -> PRDiff | None:
        return self._pr_diff

    async def get_latest_commit_sha(self, repo_owner: str, repo_name: str, pr_number: int) -> str | None:
        return "c" * 40

    def validate_repository_access(self, repo_owner: str, repo_name: str) -> bool:
        return True


def _registry(
    pr_diff: PRDiff | None = None,
    *,
    fail: Exception | None = None,
    cache: RecordingCache | None = None,
) -> ToolRegistry:
    logger = MagicMock()
    rate = MagicMock()
    rate.check_rate_limit.return_value = True
    rate.get_rate_limit_info.return_value = {"max_requests": 100, "window_seconds": 60}
    metrics = MagicMock()
    metrics.generate_request_id.return_value = "req-1"
    metrics.track_request = MagicMock()
    auth = MagicMock()
    auth.authenticate.return_value = (True, "client-1")
    validator = MagicMock()
    validator.sanitize_string.side_effect = lambda s, max_length=1000: s
    validator.validate_github_url.return_value = ("owner", "repo", 1)
    validator.validate_gitlab_url.return_value = ("group/sub", "project", 42)

    cache_service = cache or RecordingCache()
    pr_diff_service = FakeReader(pr_diff)
    registry = ToolRegistry(
        pr_diff_service=pr_diff_service,
        cache_service=cache_service,
        logger=logger,
        provider_resolver=create_provider_capability_resolver(
            github_reader=pr_diff_service,
            github_repository_factory=MagicMock(),
            gitlab_reader=None,
            gitlab_operations=None,
        ),
        rate_limiter=rate,
        metrics_tracker=metrics,
        authentication=auth,
        input_validator=validator,
        request_coalescing_service=MagicMock(),
        cache_hit_optimization_enabled=False,
    )
    registry._recording_cache = cache_service  # type: ignore[attr-defined]
    registry._metrics = metrics  # type: ignore[attr-defined]

    async def coalesce(key, fn, timeout=None):
        if fail is not None:
            raise fail
        return pr_diff if pr_diff is not None else await fn()

    registry._request_coalescing.coalesce = AsyncMock(side_effect=coalesce)
    return registry


@pytest.mark.integration
@pytest.mark.anyio
async def test_registered_get_pr_diff_success_surface() -> None:
    """Registered tool returns ordered complete diffs with previous_path."""
    mcp = FastMCP("test-prdiffer")
    registry = _registry(_mixed_pr_diff())
    registry.register_tools(mcp)

    # Locate registered tool function
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "get_pr_diff" in names

    with patch(
        "prdiffer.application.tool_registry.parse_pr_target",
        create=True,
        return_value=MagicMock(provider="github", repo_owner="owner", repo_name="repo", pr_number=1),
    ):
        # Call tool through FastMCP call_tool if available
        result = await mcp.call_tool(
            "get_pr_diff",
            {"pr_url": "https://github.com/owner/repo/pull/1"},
        )

    # Prefer structured_content from FastMCP ToolResult
    if hasattr(result, "structured_content") and result.structured_content:
        payload = result.structured_content
    elif hasattr(result, "content") and result.content:
        import json

        payload = json.loads(result.content[0].text)
    else:
        payload = result

    files = payload.get("files", []) if isinstance(payload, dict) else []
    assert len(files) == 3
    rename = next(f for f in files if f["path"] == "new_name.py")
    assert rename["previous_path"] == "old_name.py"
    assert rename["status"] == "renamed"
    # Full-context style payload present for modified file
    mod = next(f for f in files if f["path"] == "mod.py")
    assert "context" in mod["diff"] or "old" in mod["diff"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_registered_get_pr_diff_strict_binary_failure() -> None:
    import json

    from fastmcp.exceptions import ToolError

    mcp = FastMCP("test-prdiffer-fail")
    err = FullDiffIncompleteError(
        FullDiffIncompleteReason.BINARY_CONTENT,
        path="bin.dat",
        previous_path="old.bin",
        observed=9,
        limit=8,
    )
    registry = _registry(fail=err)
    registry.register_tools(mcp)

    with patch(
        "prdiffer.application.tool_registry.parse_pr_target",
        create=True,
        return_value=MagicMock(provider="github", repo_owner="owner", repo_name="repo", pr_number=1),
    ):
        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool(
                "get_pr_diff",
                {"pr_url": "https://github.com/owner/repo/pull/1"},
            )

    # FastMCP.call_tool raises ToolError; wire protocol maps this to isError=true.
    # Body is compact JSON with stable top-level keys and safe E5020 details only.
    payload = json.loads(str(exc_info.value))
    assert list(payload.keys()) == ["error_code", "message", "details"]
    assert payload["error_code"] == "E5020_FULL_DIFF_INCOMPLETE"
    assert payload["details"]["reason"] == "BINARY_CONTENT"
    assert payload["details"]["path"] == "bin.dat"
    assert payload["details"]["previous_path"] == "old.bin"
    assert payload["details"]["observed"] == 9
    assert payload["details"]["limit"] == 8
    assert "files" not in payload
    assert "files" not in json.dumps(payload)


@pytest.mark.integration
@pytest.mark.anyio
async def test_gitlab_nested_success_and_e5020_surface() -> None:
    import json
    from fastmcp.exceptions import ToolError

    mcp = FastMCP("test-gitlab-mcp")
    success = PRDiff(
        files=(
            FileDiffResponse(
                path="new.py",
                status=EDIT_TYPE.RENAMED,
                stats=FileStats(additions=0, deletions=0),
                diff="old mode 100644\nnew mode 100755\nrename from old.py\nrename to new.py\n",
                previous_path="old.py",
            ),
        )
    )
    registry = _registry(success)
    gitlab_reader = FakeReader(success, provider="gitlab")
    registry._provider_resolver.register_strict_diff("gitlab", StrictDiffCapability(gitlab_reader, "gitlab"))
    registry.register_tools(mcp)

    with patch(
        "prdiffer.application.tool_registry.parse_pr_target",
        create=True,
        return_value=MagicMock(provider="gitlab", repo_owner="group/sub", repo_name="project", pr_number=42),
    ):
        result = await mcp.call_tool(
            "get_pr_diff",
            {"pr_url": "https://gitlab.com/group/sub/project/-/merge_requests/42"},
        )
    if hasattr(result, "structured_content") and result.structured_content:
        payload = result.structured_content
    else:
        payload = json.loads(result.content[0].text)
    files = payload.get("files", [])
    assert len(files) == 1
    assert files[0]["previous_path"] == "old.py"
    gitlab_session = await gitlab_reader.open_pr_diff_session(
        "group/sub",
        "project",
        42,
        base_url="https://gitlab.com",
    )
    try:
        gitlab_identity = gitlab_session.cache_identity
        assert gitlab_identity.cache_key == (
            "gitlab-full-diff-v1:gitlab.com:group/sub:project:42:7:"
            "dddddddddddddddddddddddddddddddddddddddd:"
            "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee:"
            "ffffffffffffffffffffffffffffffffffffffff"
        )
        assert gitlab_identity.validation_token == (
            "7:dddddddddddddddddddddddddddddddddddddddd:"
            "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee:"
            "ffffffffffffffffffffffffffffffffffffffff"
        )
        assert gitlab_identity.schema_version == 1
    finally:
        await gitlab_session.aclose()

    # E5020 path
    mcp2 = FastMCP("test-gitlab-e5020")
    err = FullDiffIncompleteError(FullDiffIncompleteReason.BINARY_CONTENT, path="x.bin")
    reg2 = _registry(fail=err)
    reg2._provider_resolver.register_strict_diff("gitlab", StrictDiffCapability(FakeReader(provider="gitlab"), "gitlab"))
    reg2.register_tools(mcp2)
    with patch(
        "prdiffer.application.tool_registry.parse_pr_target",
        create=True,
        return_value=MagicMock(provider="gitlab", repo_owner="group/sub", repo_name="project", pr_number=42),
    ):
        with pytest.raises(ToolError) as exc:
            await mcp2.call_tool(
                "get_pr_diff",
                {"pr_url": "https://gitlab.com/group/sub/project/-/merge_requests/42"},
            )
    body = json.loads(str(exc.value))
    assert body["error_code"] == "E5020_FULL_DIFF_INCOMPLETE"
    assert "files" not in body


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("reason", list(FullDiffIncompleteReason))
async def test_all_e5020_reasons_nonpartial_uncached_metric(reason: FullDiffIncompleteReason) -> None:
    import json
    from fastmcp.exceptions import ToolError

    cache = RecordingCache()
    err = FullDiffIncompleteError(reason, path="x.py", observed=1, limit=2)
    registry = _registry(fail=err, cache=cache)
    mcp = FastMCP(f"e5020-{reason.value}")
    registry.register_tools(mcp)

    with patch(
        "prdiffer.application.tool_registry.parse_pr_target",
        create=True,
        return_value=MagicMock(provider="github", repo_owner="owner", repo_name="repo", pr_number=1),
    ):
        with pytest.raises(ToolError) as ei:
            await mcp.call_tool("get_pr_diff", {"pr_url": "https://github.com/owner/repo/pull/1"})

    payload = json.loads(str(ei.value))
    assert payload["error_code"] == "E5020_FULL_DIFF_INCOMPLETE"
    assert payload["details"]["reason"] == reason.value
    assert "files" not in payload
    assert cache.sets == 0
    # One failure metric for get_pr_diff
    fail_calls = [
        c
        for c in registry._metrics.track_request.call_args_list  # type: ignore[attr-defined]
        if c.args and c.args[0] == "get_pr_diff" and c.args[1] is False
    ]
    assert len(fail_calls) == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_real_use_case_empty_success_writes_cache_once_via_coalescer() -> None:
    """Real GetPRDiffUseCase + RequestCoalescingService: empty PRDiff caches once."""
    from prdiffer.infrastructure.utils.coalescing_service import RequestCoalescingService

    empty = PRDiff(files=())
    cache = RecordingCache()

    logger = MagicMock()
    rate = MagicMock()
    rate.check_rate_limit.return_value = True
    rate.get_rate_limit_info.return_value = {"max_requests": 100, "window_seconds": 60}
    metrics = MagicMock()
    metrics.generate_request_id.return_value = "req-empty"
    metrics.track_request = MagicMock()
    auth = MagicMock()
    auth.authenticate.return_value = (True, "client-1")
    validator = MagicMock()
    validator.sanitize_string.side_effect = lambda s, max_length=1000: s
    validator.validate_github_url.return_value = ("owner", "repo", 1)

    reader = FakeReader(empty)
    registry = ToolRegistry(
        pr_diff_service=reader,
        cache_service=cache,  # type: ignore[arg-type]
        logger=logger,
        provider_resolver=create_provider_capability_resolver(
            github_reader=reader,
            github_repository_factory=MagicMock(),
            gitlab_reader=None,
            gitlab_operations=None,
        ),
        rate_limiter=rate,
        metrics_tracker=metrics,
        authentication=auth,
        input_validator=validator,
        request_coalescing_service=RequestCoalescingService(max_waiters=10),
        cache_hit_optimization_enabled=False,
    )
    mcp = FastMCP("empty-real-uc")
    registry.register_tools(mcp)

    with patch(
        "prdiffer.application.tool_registry.parse_pr_target",
        create=True,
        return_value=MagicMock(provider="github", repo_owner="owner", repo_name="repo", pr_number=1),
    ):
        result = await mcp.call_tool("get_pr_diff", {"pr_url": "https://github.com/owner/repo/pull/1"})
        # Second call should hit cache (no second set)
        result2 = await mcp.call_tool("get_pr_diff", {"pr_url": "https://github.com/owner/repo/pull/1"})

    if hasattr(result, "structured_content") and result.structured_content:
        payload = result.structured_content
    else:
        import json

        payload = json.loads(result.content[0].text)
    assert payload.get("files") == []
    assert cache.sets == 1
    del result2  # hit path exercised; sets stay at 1
    assert cache.sets == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_authoritative_empty_success_writes_cache_once() -> None:
    """Empty complete PRDiff is success and may be cached by use-case path.

    This surface stubs coalescing to return empty PRDiff; cache write is not
    exercised here — assert success payload has empty files and zero error.
    """
    mcp = FastMCP("empty-ok")
    empty = PRDiff(files=())
    registry = _registry(empty)
    registry.register_tools(mcp)
    with patch(
        "prdiffer.application.tool_registry.parse_pr_target",
        create=True,
        return_value=MagicMock(provider="github", repo_owner="owner", repo_name="repo", pr_number=1),
    ):
        result = await mcp.call_tool("get_pr_diff", {"pr_url": "https://github.com/owner/repo/pull/1"})
    if hasattr(result, "structured_content") and result.structured_content:
        payload = result.structured_content
    else:
        import json

        payload = json.loads(result.content[0].text)
    assert payload.get("files") == []
    ok_calls = [
        c
        for c in registry._metrics.track_request.call_args_list  # type: ignore[attr-defined]
        if c.args and c.args[0] == "get_pr_diff" and c.args[1] is True
    ]
    assert len(ok_calls) == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_operational_rate_limit_not_remapped_to_e5020() -> None:
    import json
    from fastmcp.exceptions import ToolError
    from prdiffer.domain.exceptions import RateLimitError
    from prdiffer.domain.error_codes import E3001_RATE_LIMITED

    cache = RecordingCache()
    err = RateLimitError("slow down", retry_after=30, error_code=E3001_RATE_LIMITED)
    registry = _registry(fail=err, cache=cache)
    mcp = FastMCP("rate-limit")
    registry.register_tools(mcp)
    with patch(
        "prdiffer.application.tool_registry.parse_pr_target",
        create=True,
        return_value=MagicMock(provider="github", repo_owner="owner", repo_name="repo", pr_number=1),
    ):
        with pytest.raises(ToolError) as ei:
            await mcp.call_tool("get_pr_diff", {"pr_url": "https://github.com/owner/repo/pull/1"})
    body = str(ei.value)
    # Should not be structured E5020 incomplete
    if body.strip().startswith("{"):
        payload = json.loads(body)
        assert payload.get("error_code") != "E5020_FULL_DIFF_INCOMPLETE"
    else:
        assert "E5020" not in body
    assert cache.sets == 0
