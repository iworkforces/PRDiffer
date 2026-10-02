from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import unwrap_pr_diff_cache_value
from prdiffer.domain.interfaces.pr_diff_reader import SessionPRDiffReader
from prdiffer.domain.services.cache import CacheServiceInterface


class GetPRDiffUseCase:
    """Use case for getting PR diff data with automatic caching.

    Opens one request session, uses ``session.cache_identity`` for cache
    selection, and always closes the session.
    """

    def __init__(
        self,
        pr_diff_service: SessionPRDiffReader,
        cache_service: CacheServiceInterface,
        cache_hit_optimization_enabled: bool = False,
    ):
        self._pr_diff_service: SessionPRDiffReader = pr_diff_service
        self._cache_service: CacheServiceInterface = cache_service
        self._cache_hit_optimization_enabled = cache_hit_optimization_enabled

    async def execute(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        *,
        base_url: str | None = None,
    ) -> PRDiff:
        """Execute the use case with automatic snapshot-identity caching.

        ``base_url`` is optional and used by GitLab session readers for
        custom-hosted instances (e.g. ``https://gitlab.example.com``).
        """
        # All session readers accept base_url (GitHub ignores; GitLab uses it).
        session = await self._pr_diff_service.open_pr_diff_session(repo_owner, repo_name, pr_number, base_url=base_url)
        try:
            identity = session.cache_identity
            cache_key = identity.cache_key
            validation_token = identity.validation_token

            if self._cache_hit_optimization_enabled:
                cached_result, cached_token = await self._cache_service.get_optimistic(cache_key)
                unwrapped = unwrap_pr_diff_cache_value(cached_result, key=cache_key, identity=identity) if cached_result is not None else None
                if unwrapped is not None and cached_token and cached_token == validation_token:
                    return unwrapped

            cached_result = await self._cache_service.get(cache_key, validation_token)
            unwrapped = unwrap_pr_diff_cache_value(cached_result, key=cache_key, identity=identity) if cached_result is not None else None
            if unwrapped is not None:
                return unwrapped

            result = await session.build_pr_diff()
            # Authoritative empty PRDiff(files=()) still caches.
            await self._cache_service.set(cache_key, validation_token, result)
            return result
        finally:
            await session.aclose()
