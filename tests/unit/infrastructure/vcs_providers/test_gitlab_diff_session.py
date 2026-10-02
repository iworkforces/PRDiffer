"""Tests for GitLab strict PR diff session lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import GITLAB_FULL_DIFF_CACHE_PREFIX
from prdiffer.domain.error_codes import E1001_INVALID_URL
from prdiffer.domain.exceptions import (
    FullDiffIncompleteError,
    FullDiffIncompleteReason,
    InvalidURLError,
    PRDifferException,
)
from prdiffer.infrastructure.vcs_providers.gitlab_diff_session import GitLabPRDiffSession, GitLabSessionPRDiffReader
from prdiffer.infrastructure.vcs_providers.gitlab_models import GitLabDiffSnapshot


def _snapshot(**kwargs: object) -> GitLabDiffSnapshot:
    state = str(kwargs.get("state", "empty"))
    base = str(kwargs.get("base_sha", "same"))
    head = str(kwargs.get("head_sha", "same" if state == "empty" else "head"))
    return GitLabDiffSnapshot(
        project_path=str(kwargs.get("project_path", "group/sub/project")),
        iid=int(kwargs.get("iid", 42)),
        version_id=int(kwargs.get("version_id", 7)),
        base_sha=base,
        start_sha=str(kwargs.get("start_sha", "start")),
        head_sha=head,
        state=state,
        real_size=int(kwargs["real_size"]) if "real_size" in kwargs else (0 if state == "empty" else 0),
        records=(),
    )


@pytest.mark.unit
@pytest.mark.anyio
class TestGitLabPRDiffSession:
    async def test_cache_identity_matches_snapshot(self) -> None:
        snap = _snapshot()
        session = GitLabPRDiffSession(
            snapshot=snap,
            operations=MagicMock(),
            content_fetcher=MagicMock(),
            assembler=MagicMock(),
            config=GitLabConfig(),
            runtime=MagicMock(),
            deadline_monotonic=1e18,
        )
        identity = session.cache_identity
        assert identity.cache_key.startswith(GITLAB_FULL_DIFF_CACHE_PREFIX)
        assert "group/sub" in identity.cache_key or "group/sub".casefold() in identity.cache_key
        assert identity.validation_token == "7:same:start:same"
        assert session.snapshot.head_sha == "same"
        await session.aclose()
        await session.aclose()  # idempotent

    async def test_cache_identity_includes_port_aware_host(self) -> None:
        snap = _snapshot()
        session = GitLabPRDiffSession(
            snapshot=snap,
            operations=MagicMock(),
            content_fetcher=MagicMock(),
            assembler=MagicMock(),
            config=GitLabConfig(allowed_hosts=("gitlab.example.com",)),
            runtime=MagicMock(),
            deadline_monotonic=1e18,
            base_url="https://gitlab.example.com:8443",
        )
        assert "gitlab.example.com:8443" in session.cache_identity.cache_key
        await session.aclose()

    async def test_build_forwards_base_url_and_deadline_to_content(self) -> None:
        snap = _snapshot(state="empty")
        content = MagicMock()
        content.fetch_all = AsyncMock(return_value=())
        assembler = MagicMock()
        assembler.assemble.return_value = PRDiff(files=())
        session = GitLabPRDiffSession(
            snapshot=snap,
            operations=MagicMock(),
            content_fetcher=content,
            assembler=assembler,
            config=GitLabConfig(allowed_hosts=("gitlab.example.com",)),
            runtime=MagicMock(),
            deadline_monotonic=1e18,
            base_url="https://gitlab.example.com",
        )
        await session.build_pr_diff()
        content.fetch_all.assert_awaited_once()
        kwargs = content.fetch_all.await_args.kwargs
        assert kwargs["base_url"] == "https://gitlab.example.com"
        assert kwargs["deadline_monotonic"] == 1e18
        await session.aclose()

    async def test_build_once_and_close_blocks_second_build(self) -> None:
        snap = _snapshot(state="empty")
        content = MagicMock()
        content.fetch_all = AsyncMock(return_value=())
        assembler = MagicMock()
        assembler.assemble.return_value = PRDiff(files=())
        session = GitLabPRDiffSession(
            snapshot=snap,
            operations=MagicMock(),
            content_fetcher=content,
            assembler=assembler,
            config=GitLabConfig(),
            runtime=MagicMock(),
            deadline_monotonic=1e18,
        )
        result = await session.build_pr_diff()
        assert isinstance(result, PRDiff)
        with pytest.raises(PRDifferException):
            await session.build_pr_diff()
        await session.aclose()
        with pytest.raises(PRDifferException):
            await session.build_pr_diff()

    async def test_build_failure_still_allows_close(self) -> None:
        snap = GitLabDiffSnapshot(
            project_path="g/p",
            iid=1,
            version_id=1,
            base_sha="b",
            start_sha="s",
            head_sha="h",
            state="collected",
            real_size=1,
            records=(),
        )
        session = GitLabPRDiffSession(
            snapshot=snap,
            operations=MagicMock(),
            content_fetcher=MagicMock(),
            assembler=MagicMock(),
            config=GitLabConfig(),
            runtime=MagicMock(),
            deadline_monotonic=1e18,
        )
        with pytest.raises(FullDiffIncompleteError) as exc:
            await session.build_pr_diff()
        assert exc.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED
        await session.aclose()
        assert session._closed is True


@pytest.mark.unit
@pytest.mark.anyio
class TestGitLabSessionReader:
    async def test_open_build_close_lifecycle(self) -> None:
        ops = MagicMock()
        ops.select_with_client.return_value = _snapshot(state="empty")
        content = MagicMock()
        content.fetch_all = AsyncMock(return_value=())
        assembler = MagicMock()
        assembler.assemble.return_value = PRDiff(
            files=(
                FileDiffResponse(
                    path="x.py",
                    status=EDIT_TYPE.ADDED,
                    stats=FileStats(additions=0, deletions=0),
                    diff="",
                ),
            )
        )

        async def run_blocking(callback, **kwargs):  # type: ignore[no-untyped-def]
            client = MagicMock()
            return callback(client)

        runtime = MagicMock()
        runtime.ensure_host_allowed = MagicMock()
        runtime.run_blocking = AsyncMock(side_effect=run_blocking)

        reader = GitLabSessionPRDiffReader(
            operations=ops,
            runtime=runtime,
            content_fetcher=content,
            assembler=assembler,
            config=GitLabConfig(),
        )
        session = await reader.open_pr_diff_session("group/sub", "project", 42)
        try:
            result = await session.build_pr_diff()
        finally:
            await session.aclose()
        assert result is not None
        runtime.ensure_host_allowed.assert_called()
        runtime.run_blocking.assert_awaited()
        # Content receives base_url + deadline from session
        content.fetch_all.assert_awaited()
        kwargs = content.fetch_all.await_args.kwargs
        assert kwargs["base_url"] == "https://gitlab.com"
        assert kwargs["deadline_monotonic"] is not None

    async def test_open_rejects_disallowed_host(self) -> None:
        runtime = MagicMock()
        runtime.ensure_host_allowed.side_effect = InvalidURLError(
            "not allowed",
            error_code=E1001_INVALID_URL,
            details={"host": "evil.internal"},
        )
        reader = GitLabSessionPRDiffReader(
            operations=MagicMock(),
            runtime=runtime,
            content_fetcher=MagicMock(),
            assembler=MagicMock(),
            config=GitLabConfig(),
        )
        with pytest.raises(InvalidURLError) as exc:
            await reader.open_pr_diff_session("o", "r", 1, base_url="https://evil.internal")
        assert exc.value.error_code is E1001_INVALID_URL
        runtime.run_blocking.assert_not_called()

    async def test_open_forwards_custom_base_url_to_runtime(self) -> None:
        ops = MagicMock()
        ops.select_with_client.return_value = _snapshot(state="empty")

        async def run_blocking(callback, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["base_url"] == "https://gitlab.example.com"
            return callback(MagicMock())

        runtime = MagicMock()
        runtime.ensure_host_allowed = MagicMock()
        runtime.run_blocking = AsyncMock(side_effect=run_blocking)
        reader = GitLabSessionPRDiffReader(
            operations=ops,
            runtime=runtime,
            content_fetcher=MagicMock(),
            assembler=MagicMock(),
            config=GitLabConfig(allowed_hosts=("gitlab.example.com",)),
        )
        session = await reader.open_pr_diff_session("g", "p", 1, base_url="https://gitlab.example.com")
        runtime.ensure_host_allowed.assert_called_with("https://gitlab.example.com")
        assert session.cache_identity.cache_key.startswith(GITLAB_FULL_DIFF_CACHE_PREFIX)
        assert "gitlab.example.com" in session.cache_identity.cache_key
        await session.aclose()
