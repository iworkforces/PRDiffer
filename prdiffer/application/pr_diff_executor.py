"""Coalesced PR diff execution support for application request boundaries."""

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.error_codes import E5002_GITHUB_API_ERROR
from prdiffer.domain.exceptions import GitHubAPIError
from prdiffer.domain.interfaces.request_coalescing import RequestCoalescingProtocol
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.pr_diff_service import PRDiffServiceInterface
from prdiffer.domain.usecases.pr_diff_usecases import GetPRDiffUseCase, PRDiffReader


class CoalescedPRDiffExecutionMixin:
    _pr_diff_service: PRDiffServiceInterface
    _cache_service: CacheServiceInterface
    _logger: LoggerServiceInterface
    _request_coalescing: RequestCoalescingProtocol
    _cache_hit_optimization_enabled: bool
    _pr_diff_request_timeout_seconds: float | None

    def _resolve_pr_diff_request_timeout(self) -> float:
        """Return owner deadline for coalesced PR diff work (seconds)."""
        configured = getattr(self, "_pr_diff_request_timeout_seconds", None)
        if configured is not None:
            return float(configured)
        service_timeout = getattr(self._pr_diff_service, "_pr_diff_request_timeout_seconds", None)
        if service_timeout is not None:
            return float(service_timeout)
        return 180.0

    async def _execute_use_case_with_coalescing(
        self,
        request_id: str,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        *,
        pr_diff_reader: PRDiffReader | None = None,
        cache_namespace: str | None = None,
        base_url: str | None = None,
    ) -> PRDiff:
        # Stampede control only: same PR URL collapses concurrent in-flight owners.
        # Snapshot identity (merge_base:head / GitLab version pin) is established
        # inside the owner fetch and used for cache keys — not the coalesce key.
        # Waiters intentionally share one open/build result for the owner lifetime.
        coalesce_key = f"{repo_owner}/{repo_name}/pr/{pr_number}"
        if cache_namespace:
            coalesce_key = f"{cache_namespace}:{coalesce_key}"
        if base_url:
            coalesce_key = f"{base_url.rstrip('/')}:{coalesce_key}"
        reader = self._pr_diff_service if pr_diff_reader is None else pr_diff_reader
        owner_deadline = self._resolve_pr_diff_request_timeout()

        async def fetch_pr_diff() -> PRDiff:
            """Fetch PR diff - will be coalesced if multiple requests arrive."""
            use_case = GetPRDiffUseCase(
                pr_diff_service=reader,
                cache_service=self._cache_service,
                cache_hit_optimization_enabled=self._cache_hit_optimization_enabled,
                cache_namespace=cache_namespace,
            )
            result = await use_case.execute(
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                base_url=base_url,
            )

            if result is None:
                self._logger.error(
                    "Use case returned None for PR diff",
                    request_id=request_id,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    pr_number=pr_number,
                )
                raise GitHubAPIError(
                    "Failed to get PR diff - use case returned None",
                    error_code=E5002_GITHUB_API_ERROR,
                )

            return result

        return await self._request_coalescing.coalesce(
            coalesce_key,
            fetch_pr_diff,
            timeout=owner_deadline,
        )
