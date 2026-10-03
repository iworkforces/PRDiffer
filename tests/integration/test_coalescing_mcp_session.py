"""Owner cancellation isolation through one real in-memory MCP session."""

from typing import Any
from collections.abc import Awaitable, Callable
from unittest.mock import Mock

import anyio
import pytest
from fastmcp import Client, FastMCP

from prdiffer.application.provider_resolver import create_provider_capability_resolver
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffReadSessionInterface
from prdiffer.infrastructure.utils.coalescing_service import CoalescedRequest, RequestCoalescingService
from tests.integration.test_full_diff_mcp_surface import FakeReader, ProviderName, _registry


class SessionCoalescer(RequestCoalescingService):
    def __init__(self) -> None:
        super().__init__(logger=Mock(), max_waiters=50)
        self.joined = anyio.Event()
        self.owner_cancelled = anyio.Event()

    async def _wait_for_request(self, existing_request: CoalescedRequest, key: str, timeout: float) -> Any:
        self.joined.set()
        return await super()._wait_for_request(existing_request, key, timeout)

    async def _execute_request(self, new_request: CoalescedRequest, key: str, fetch_func: Callable[[], Awaitable[Any]], timeout: float) -> Any:
        try:
            return await super()._execute_request(new_request, key, fetch_func, timeout)
        except anyio.get_cancelled_exc_class():
            self.owner_cancelled.set()
            raise


class BlockingReader(FakeReader):
    def __init__(self) -> None:
        super().__init__()
        self.started = anyio.Event()
        self.unrelated_started = anyio.Event()
        self.unrelated_release = anyio.Event()
        self.shared_calls = 0

    async def open_pr_diff_session(self, repo_owner: str, repo_name: str, pr_number: int, /, *, base_url: str | None = None) -> PRDiffReadSessionInterface:
        if pr_number == 1:
            self.shared_calls += 1
            self.started.set()
            await anyio.Event().wait()
        else:
            self.unrelated_started.set()
            await self.unrelated_release.wait()
        return await super().open_pr_diff_session(repo_owner, repo_name, pr_number, base_url=base_url)


@pytest.mark.integration
@pytest.mark.anyio
async def test_registered_owner_cancellation_preserves_same_mcp_session() -> None:
    service, reader = SessionCoalescer(), BlockingReader()
    registry = _registry()
    registry._request_coalescing = service
    registry._provider_resolver = create_provider_capability_resolver(
        github_reader=reader, github_repository_factory=Mock(), gitlab_reader=None, gitlab_operations=None
    )
    validator = Mock()
    validator.sanitize_string.side_effect = lambda s, max_length=1000: s
    validator.validate_github_url.side_effect = lambda url: ("owner", "repo", int(url.rsplit("/", 1)[1]))
    registry._input_validator = validator
    mcp = FastMCP("coalescer-session")
    registry.register_tools(mcp)
    owner_scope = anyio.CancelScope()
    owner_done, waiter_done, unrelated_done = anyio.Event(), anyio.Event(), anyio.Event()
    responses = {}

    with anyio.fail_after(5):
        async with Client(mcp) as client:
            session = client.session

            async def owner() -> None:
                with owner_scope:
                    try:
                        await client.call_tool_mcp("get_pr_diff", {"pr_url": "https://github.com/owner/repo/pull/1"})
                    finally:
                        owner_done.set()

            async def request(name: str, number: int, done: anyio.Event) -> None:
                responses[name] = await client.call_tool_mcp("get_pr_diff", {"pr_url": f"https://github.com/owner/repo/pull/{number}"})
                done.set()

            async with anyio.create_task_group() as group:
                group.start_soon(owner)
                await reader.started.wait()
                group.start_soon(request, "waiter", 1, waiter_done)
                await service.joined.wait()
                group.start_soon(request, "unrelated", 2, unrelated_done)
                await reader.unrelated_started.wait()
                owner_scope.cancel()
                await owner_done.wait()
                await service.owner_cancelled.wait()
                await waiter_done.wait()
                assert responses["waiter"].is_error
                reader.unrelated_release.set()
                await unrelated_done.wait()
                assert not responses["unrelated"].is_error
                assert client.session is session
                assert "get_pr_diff" in {tool.name for tool in await client.list_tools()}
    assert reader.shared_calls == 1
    assert await service.get_stats() == {"pending_count": 0, "pending_keys": [], "total_waiters": 0}


@pytest.mark.integration
@pytest.mark.anyio
async def test_production_caller_preserves_provider_namespace_and_normalized_host_keys() -> None:
    service = SessionCoalescer()
    registry = _registry()
    registry._request_coalescing = service
    release, started = anyio.Event(), anyio.Event()
    opened: list[str | None] = []
    results = []

    class Reader(FakeReader):
        async def open_pr_diff_session(self, repo_owner: str, repo_name: str, pr_number: int, /, *, base_url: str | None = None) -> PRDiffReadSessionInterface:
            opened.append(base_url)
            if len(opened) == 4:
                started.set()
            await release.wait()
            return await super().open_pr_diff_session(repo_owner, repo_name, pr_number, base_url=base_url)

    async def request(namespace: ProviderName, base_url: str | None) -> None:
        results.append(await registry._execute_use_case_with_coalescing(
            "group/sub", "project", 42, pr_diff_reader=Reader(provider=namespace), cache_namespace=namespace, base_url=base_url
        ))

    with anyio.fail_after(2):
        async with anyio.create_task_group() as group:
            group.start_soon(request, "github", None)
            group.start_soon(request, "gitlab", None)
            group.start_soon(request, "gitlab", "https://gitlab.example")
            group.start_soon(request, "gitlab", "https://other.example")
            await started.wait()
            group.start_soon(request, "gitlab", "https://gitlab.example/")
            await service.joined.wait()
            stats = await service.get_stats()
            assert stats["pending_count"] == 4
            assert stats["total_waiters"] == 5
            assert set(stats["pending_keys"]) == {
                "github:group/sub/project/pr/42", "gitlab:group/sub/project/pr/42",
                "https://gitlab.example:gitlab:group/sub/project/pr/42", "https://other.example:gitlab:group/sub/project/pr/42",
            }
            release.set()
    assert len(results) == 5
    assert len(opened) == 4
    assert all(result.files == () for result in results)
    assert await service.get_stats() == {"pending_count": 0, "pending_keys": [], "total_waiters": 0}
