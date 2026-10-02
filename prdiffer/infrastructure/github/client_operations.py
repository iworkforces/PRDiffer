"""GitHub API client operations (file content, caching, batch fetching).

Extracted from client.py for maintainability.
Contains file content retrieval, caching, and batch operations.
"""

from __future__ import annotations

import time
import asyncer
from collections import OrderedDict
from typing import Any

from github import Github
from github.ContentFile import ContentFile

from prdiffer.infrastructure.utils.retry.models import OperationContext
from prdiffer.infrastructure.utils.retry.base import BaseUnifiedRetryHandler
from prdiffer.infrastructure.logging.console_logger import ConsoleLogger
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)

from prdiffer.domain.entities.file_content import (
    FileContentAvailable,
    FileContentRequest,
    FileContentResponse,
    FileContentResult,
    FileContentUnavailable,
    FileContentUnavailableReason,
)
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.domain.error_codes import E5002_GITHUB_API_ERROR, E5009_CONFIGURATION_ERROR
from prdiffer.infrastructure.github.client_models import GITHUB_API_EXCEPTIONS
from prdiffer.infrastructure.utils.parallel.executor import AsyncParallelExecutor
from prdiffer.infrastructure.github.etag_adapter import ETagRequestAdapter

# Cache key: (repo_full_name, path, immutable_ref)
FileContentCacheKey = tuple[str, str, str]


class GitHubAPIClientOperationsMixin:
    """Mixin providing file content operations and caching for GitHubAPIClient.

    Requires the host class to provide:
        - self._github_client: Github | None
        - self._logger: ConsoleLogger
        - self._retry_handler: retry handler
        - self._file_content_cache: OrderedDict
        - self._cache_max_size: int
        - self._cache_ttl: int
        - self._max_file_size_bytes: int
        - self._cache_hits: int
        - self._cache_misses: int
        - self._cache_evictions: int
        - self._cache_evictions_ttl: int
        - self._cache_evictions_size: int
        - self._async_executor: AsyncParallelExecutor
        - self._etag_request_adapter: ETagRequestAdapter
        - self._parallel_file_fetch_enabled: bool
    """

    # Type annotations for host class attributes used by this mixin
    _github_client: Github | None
    _retry_handler: BaseUnifiedRetryHandler
    _logger: ConsoleLogger
    _file_content_cache: OrderedDict[FileContentCacheKey, dict[str, Any]]
    _cache_ttl: float
    _cache_max_size: int
    _cache_hits: int
    _cache_misses: int
    _cache_evictions: int
    _cache_evictions_ttl: int
    _cache_evictions_size: int
    _parallel_file_fetch_enabled: bool
    _async_executor: AsyncParallelExecutor
    _max_file_size_bytes: int
    _etag_request_adapter: ETagRequestAdapter

    def _content_cache_key(self, repo_full_name: str, file_path: str, ref: str) -> FileContentCacheKey:
        return (repo_full_name, file_path, ref)

    def _evict_oldest_entries(self) -> None:
        current_time = time.time()

        expired_keys: list[FileContentCacheKey] = []
        for key, entry in self._file_content_cache.items():
            age = current_time - float(entry["timestamp"])
            if age >= self._cache_ttl:
                expired_keys.append(key)

        for key in expired_keys:
            self._file_content_cache.pop(key)
            self._cache_evictions_ttl += 1

        if expired_keys:
            self._logger.debug(
                f"Cache eviction (TTL): removed {len(expired_keys)} expired entries [size={len(self._file_content_cache)}/{self._cache_max_size}]"
            )

        while len(self._file_content_cache) >= self._cache_max_size:
            evicted_key, _ = self._file_content_cache.popitem(last=False)
            self._cache_evictions_size += 1
            self._cache_evictions += 1
            self._logger.debug(f"Cache eviction (LRU): {evicted_key[1][:50]}... [size={len(self._file_content_cache)}/{self._cache_max_size}]")

    def _normalize_cache_key(self, cache_key: tuple[str, ...] | FileContentCacheKey) -> FileContentCacheKey:
        """Accept legacy (path, ref) or full (repo, path, ref) keys."""
        if len(cache_key) == 3:
            return cache_key
        if len(cache_key) == 2:
            path, ref = cache_key
            return ("", path, ref)
        raise ValueError(f"Invalid content cache key: {cache_key!r}")

    def _is_cache_entry_valid(self, cache_key: tuple[str, ...] | FileContentCacheKey) -> bool:
        key = self._normalize_cache_key(cache_key)
        if key not in self._file_content_cache:
            return False
        entry = self._file_content_cache[key]
        age = time.time() - float(entry["timestamp"])
        return bool(age < self._cache_ttl)

    def _cache_set_available(self, cache_key: FileContentCacheKey | tuple[str, ...], content: str) -> None:
        """Cache only available text content — never unavailable sentinels."""
        key = self._normalize_cache_key(cache_key)
        if key in self._file_content_cache:
            del self._file_content_cache[key]
        else:
            self._evict_oldest_entries()

        self._file_content_cache[key] = {
            "content": content,
            "timestamp": time.time(),
        }

    def _cache_get_available(self, cache_key: FileContentCacheKey | tuple[str, ...]) -> str | None:
        key = self._normalize_cache_key(cache_key)
        if not self._is_cache_entry_valid(key):
            if key in self._file_content_cache:
                del self._file_content_cache[key]
            self._cache_misses += 1
            return None

        self._file_content_cache.move_to_end(key)
        self._cache_hits += 1
        return str(self._file_content_cache[key]["content"])

    # Compatibility aliases used by older tests/call sites.
    def _cache_set(self, cache_key: tuple[str, ...] | FileContentCacheKey, content: str) -> None:
        self._cache_set_available(cache_key, content)

    def _cache_get(self, cache_key: tuple[str, ...] | FileContentCacheKey) -> str | None:
        return self._cache_get_available(cache_key)

    def _extract_file_content(self, content: ContentFile) -> str:
        """Legacy string extractor (empty string on deterministic unavailability)."""
        path = str(getattr(content, "path", "unknown") or "unknown")
        result = self._extract_file_content_result(content, path, "")
        if isinstance(result, FileContentAvailable):
            return result.text
        return ""

    def _unavailable(
        self,
        reason: FileContentUnavailableReason,
        path: str,
        ref: str,
        observed_size: int | None = None,
    ) -> FileContentUnavailable:
        return FileContentUnavailable(reason=reason, path=path, ref=ref, observed_size=observed_size)

    def _is_not_found(self, exc: BaseException) -> bool:
        status = getattr(exc, "status", None)
        if status == 404:
            return True
        message = str(exc).lower()
        return "404" in message and ("not found" in message or "not exist" in message)

    def get_file_content(self, repo_full_name: str, file_path: str, branch: str) -> FileContentResult:
        if not self._github_client:
            raise PRDifferException(
                "GitHub client not initialized. Call initialize_client() before using get_file_content().",
                error_code=E5009_CONFIGURATION_ERROR,
            )

        cache_key = self._content_cache_key(repo_full_name, file_path, branch)
        cached_content = self._cache_get_available(cache_key)
        if cached_content is not None:
            return FileContentAvailable(text=cached_content)

        try:
            pygithub_repo = self._retry_handler.execute_with_retry(
                self._github_client.get_repo,
                repo_full_name,
                context=OperationContext.REPOSITORY_ACCESS,
            )
            if not pygithub_repo:
                raise PRDifferException(
                    f"Repository not accessible: {repo_full_name}",
                    error_code=E5002_GITHUB_API_ERROR,
                )

            content = self._retry_handler.execute_with_retry(
                pygithub_repo.get_contents,
                file_path,
                ref=branch,
                context=OperationContext.FILE_CONTENT,
            )

            if isinstance(content, list):
                directory_count = content.__len__()
                self._logger.warning(f"Expected single file but got directory for path '{file_path}' in branch '{branch}'. Found {directory_count} items.")
                return self._unavailable(FileContentUnavailableReason.DIRECTORY, file_path, branch)

            result = self._extract_file_content_result(content, file_path, branch)
            if isinstance(result, FileContentAvailable):
                self._cache_set_available(cache_key, result.text)
            return result

        except GITHUB_API_EXCEPTIONS as e:
            if self._is_not_found(e):
                return self._unavailable(FileContentUnavailableReason.NOT_FOUND, file_path, branch)
            if not isinstance(e, Exception):
                raise
            # Operational failures (auth, rate limit, transport, retry exhaustion) propagate.
            sanitized = sanitize_exception_for_logging(e)
            self._logger.warning(
                f"Operational failure fetching file '{file_path}' in branch '{branch}'",
                extra=sanitized,
            )
            raise

    def get_files_content_batch(
        self,
        repo_full_name: str,
        file_paths: list[str],
        branch: str,
    ) -> dict[str, FileContentResult]:
        """Get typed content for multiple files with caching and optional parallel fetching."""
        requests = tuple(FileContentRequest(repo_full_name, file_path, branch) for file_path in file_paths)
        return {response.request.path: response.content for response in self.get_files_content_multi_ref_batch(requests)}

    def get_files_content_multi_ref_batch(self, requests: tuple[FileContentRequest, ...]) -> tuple[FileContentResponse, ...]:
        """Get ref-qualified content in request order without partial responses."""
        if self._parallel_file_fetch_enabled:
            import anyio

            return anyio.run(self._get_files_content_multi_ref_batch_parallel_async, requests)
        return self._get_files_content_multi_ref_batch_sequential(requests)

    def _get_files_content_multi_ref_batch_sequential(self, requests: tuple[FileContentRequest, ...]) -> tuple[FileContentResponse, ...]:
        content_by_request: dict[FileContentRequest, FileContentResult] = {}
        for request in requests:
            if request in content_by_request:
                continue
            cache_key = self._content_cache_key(request.repo_full_name, request.path, request.ref)
            cached_content = self._cache_get_available(cache_key)
            content_by_request[request] = (
                FileContentAvailable(text=cached_content)
                if cached_content is not None
                else self.get_file_content(request.repo_full_name, request.path, request.ref)
            )
        return tuple(FileContentResponse(request=request, content=content_by_request[request]) for request in requests)

    async def _get_file_content_async(
        self,
        repo_full_name: str,
        file_path: str,
        branch: str,
    ) -> FileContentResult:
        if not self._github_client:
            raise PRDifferException(
                "GitHub client not initialized. Call initialize_client() before using _get_file_content_async().",
                error_code=E5009_CONFIGURATION_ERROR,
            )

        github_client = self._github_client

        cache_key = self._content_cache_key(repo_full_name, file_path, branch)
        cached_content = self._cache_get_available(cache_key)
        if cached_content is not None:
            return FileContentAvailable(text=cached_content)

        try:

            async def get_repo_async():
                return await asyncer.asyncify(
                    lambda: self._retry_handler.execute_with_retry(
                        github_client.get_repo,
                        repo_full_name,
                        context=OperationContext.REPOSITORY_ACCESS,
                    )
                )()

            pygithub_repo = await get_repo_async()
            if not pygithub_repo:
                raise PRDifferException(
                    f"Repository not accessible: {repo_full_name}",
                    error_code=E5002_GITHUB_API_ERROR,
                )

            async def get_contents_async():
                return await asyncer.asyncify(
                    lambda: self._retry_handler.execute_with_retry(
                        pygithub_repo.get_contents,
                        file_path,
                        ref=branch,
                        context=OperationContext.FILE_CONTENT,
                    )
                )()

            content = await get_contents_async()

            if isinstance(content, list):
                directory_count = content.__len__()
                self._logger.warning(f"Expected single file but got directory for path '{file_path}' in branch '{branch}'. Found {directory_count} items.")
                return self._unavailable(FileContentUnavailableReason.DIRECTORY, file_path, branch)

            result = self._extract_file_content_result(content, file_path, branch)
            if isinstance(result, FileContentAvailable):
                self._cache_set_available(cache_key, result.text)
            return result

        except GITHUB_API_EXCEPTIONS as e:
            if self._is_not_found(e):
                return self._unavailable(FileContentUnavailableReason.NOT_FOUND, file_path, branch)
            if not isinstance(e, Exception):
                raise
            sanitized = sanitize_exception_for_logging(e)
            self._logger.warning(
                f"Operational failure fetching file '{file_path}' in branch '{branch}'",
                extra=sanitized,
            )
            raise

    async def _get_file_content_request_async(self, request: FileContentRequest) -> FileContentResult:
        return await self._get_file_content_async(request.repo_full_name, request.path, request.ref)

    async def _get_files_content_multi_ref_batch_parallel_async(
        self,
        requests: tuple[FileContentRequest, ...],
    ) -> tuple[FileContentResponse, ...]:
        content_by_request: dict[FileContentRequest, FileContentResult] = {}
        misses: list[FileContentRequest] = []
        for request in requests:
            if request in content_by_request or request in misses:
                continue
            cache_key = self._content_cache_key(request.repo_full_name, request.path, request.ref)
            cached_content = self._cache_get_available(cache_key)
            if cached_content is None:
                misses.append(request)
            else:
                content_by_request[request] = FileContentAvailable(text=cached_content)

        if misses:
            fetched = await self._async_executor.execute_indexed_batch(
                self._get_file_content_request_async,
                misses,
                keys=misses,
                strict=True,
            )
            for outcome in fetched.outcomes:
                if outcome.value is None:
                    raise RuntimeError(f"Missing indexed content outcome for {outcome.key}")
                content_by_request[outcome.key] = outcome.value

        return tuple(FileContentResponse(request=request, content=content_by_request[request]) for request in requests)

    async def _get_files_content_batch_parallel_async(
        self,
        repo_full_name: str,
        file_paths: list[str],
        branch: str,
    ) -> dict[str, FileContentResult]:
        requests = tuple(FileContentRequest(repo_full_name, file_path, branch) for file_path in file_paths)
        responses = await self._get_files_content_multi_ref_batch_parallel_async(requests)
        return {response.request.path: response.content for response in responses}

    def _extract_file_content_result(
        self,
        content: ContentFile,
        file_path: str,
        ref: str,
    ) -> FileContentResult:
        """Extract typed content with size/encoding validation (DoS prevention)."""
        if not content:
            return FileContentAvailable(text="")

        encoding = getattr(content, "encoding", None)
        size = int(getattr(content, "size", 0) or 0)

        if encoding is None or encoding == "none":
            # GitHub uses encoding:none for binary, submodules; empty files may also appear.
            if size == 0:
                return FileContentAvailable(text="")
            return self._unavailable(
                FileContentUnavailableReason.BINARY_CONTENT,
                file_path,
                ref,
                observed_size=size,
            )

        if encoding != "base64":
            return self._unavailable(
                FileContentUnavailableReason.BINARY_CONTENT,
                file_path,
                ref,
                observed_size=size,
            )

        if size > self._max_file_size_bytes:
            self._logger.warning(f"File too large to load: {file_path} ({size} bytes > {self._max_file_size_bytes} bytes max).")
            return self._unavailable(
                FileContentUnavailableReason.FILE_SIZE_LIMIT,
                file_path,
                ref,
                observed_size=size,
            )

        try:
            if content.decoded_content:
                return FileContentAvailable(text=str(content.decoded_content.decode("utf-8")))
            return FileContentAvailable(text="")
        except (UnicodeDecodeError, AttributeError, TypeError) as exc:
            self._logger.warning(
                f"Failed to decode file content for '{file_path}'",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return self._unavailable(
                FileContentUnavailableReason.CONTENT_DECODE_FAILED,
                file_path,
                ref,
                observed_size=size,
            )

    def get_etag_stats(self) -> dict[str, Any]:
        return self._etag_request_adapter.get_stats()

    def clear_etag_cache(self) -> None:
        self._etag_request_adapter.clear_cache()
