"""Coalesced PR diff execution support for application request boundaries."""

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.interfaces.pr_diff_reader import SessionPRDiffReader
from prdiffer.domain.interfaces.request_coalescing import RequestCoalescingProtocol
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.usecases.pr_diff_usecases import GetPRDiffUseCase


class CoalescedPRDiffExecutionMixin:
    _cache_service: CacheServiceInterface
    _request_coalescing: RequestCoalescingProtocol
    _pr_diff_request_timeout_seconds: float | None

    def _resolve_pr_diff_request_timeout(self) -> float:
        """Return owner deadline for coalesced PR diff work (seconds)."""
        configured = self._pr_diff_request_timeout_seconds
        if configured is not None:
            return float(configured)
        return 180.0

    async def _execute_use_case_with_coalescing(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        *,
        pr_diff_reader: SessionPRDiffReader,
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
        owner_deadline = self._resolve_pr_diff_request_timeout()

        async def fetch_pr_diff() -> PRDiff:
            """Fetch PR diff - will be coalesced if multiple requests arrive."""
            use_case = GetPRDiffUseCase(
                pr_diff_service=pr_diff_reader,
                cache_service=self._cache_service,
            )
            return await use_case.execute(
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                base_url=base_url,
            )

        return await self._request_coalescing.coalesce(
            coalesce_key,
            fetch_pr_diff,
            timeout=owner_deadline,
        )
