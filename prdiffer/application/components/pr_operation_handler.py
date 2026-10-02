from dataclasses import asdict

from typing import Any, Callable

from prdiffer.domain.interfaces.protocols import PROperationHandlerProtocol
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.repository_cache import RepositoryCacheServiceInterface
from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface
from prdiffer.domain.exceptions import (
    InvalidPRNumberError,
    InvalidRepositoryError,
    InvalidURLError,
    SuspiciousOperationError,
    ValidationError,
    GitHubAPIError,
)
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol
from prdiffer.application.utils.pr_url_parser import parse_pr_url
from prdiffer.domain.error_codes import (
    E1001_INVALID_URL,
    E5002_GITHUB_API_ERROR,
)


class PROperationHandler(PROperationHandlerProtocol):
    def __init__(
        self,
        github_repository_class: Callable[[str, str, int], PRDiffRepositoryInterface],
        cache_service: CacheServiceInterface,
        repository_cache_service: RepositoryCacheServiceInterface,
        logger: LoggerServiceInterface,
        input_validator: InputValidatorProtocol | None = None,
    ):
        self._github_repository_class = github_repository_class
        self._cache_service = cache_service
        self._repository_cache_service = repository_cache_service
        self._logger = logger
        if input_validator is None:
            from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

            input_validator = get_infrastructure_factory().create_input_validator()
        self._input_validator = input_validator

    async def get_pr_diff(self, pr_url: str) -> dict[str, Any]:
        """Get PR diff information.

        Automatic commit-based caching ensures fresh data is returned when PR changes.
        Returns structured files array response for file-level diff analysis.

        Note:
            Breaking Change: Response now uses files array instead of concatenated diff_content string.
            File metadata: path, status (added/modified/deleted/renamed/unknown),
                           stats (additions, deletions), diff (full patch content)
        """
        try:
            if not pr_url:
                raise ValidationError("PR URL parameter is required", error_code=E1001_INVALID_URL)

            try:
                repo_owner, repo_name, pr_number = parse_pr_url(pr_url, self._input_validator)
            except (
                InvalidURLError,
                InvalidRepositoryError,
                InvalidPRNumberError,
                SuspiciousOperationError,
            ) as exc:
                raise ValidationError(
                    f"Invalid GitHub PR URL format. Expected format: https://github.com/owner/repo/pull/123, got: {pr_url}",
                    error_code=E1001_INVALID_URL,
                ) from exc

            cached_repository: PRDiffRepositoryInterface | None = self._repository_cache_service.retrieve(repo_owner, repo_name, pr_number)

            repository: PRDiffRepositoryInterface
            if cached_repository is None:
                repository = self._github_repository_class(repo_owner, repo_name, pr_number)
                self._logger.debug(
                    "Created new repository instance",
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    pr_number=pr_number,
                )
            else:
                self._logger.debug(
                    "Reusing cached repository instance",
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    pr_number=pr_number,
                )
                repository = cached_repository

            await repository.initialize()

            # Execute the repository directly (since we don't have a PRDiffService)
            pr_diff: PRDiff = await repository.get_pr_diff()

            # Cache the repository after it's been used (now it should be initialized)
            if getattr(repository, "_initialized", False):
                cache_success = self._repository_cache_service.insert(repository)
                if cache_success:
                    self._logger.debug(
                        "Cached repository instance after initialization",
                        repo_owner=repo_owner,
                        repo_name=repo_name,
                        pr_number=pr_number,
                    )

            response = asdict(pr_diff)
            response["files"] = list(response["files"])
            self._logger.info(
                "Successfully fetched PR diff",
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
            )
            return response

        except (ValueError, ValidationError) as e:
            self._logger.warning(
                "Validation error in PR diff request",
                pr_url=pr_url,
                error=str(e),
            )
            raise ValidationError(f"Invalid request: {e}", error_code=E1001_INVALID_URL)
        except Exception as e:
            self._logger.error(
                "Failed to fetch PR diff",
                pr_url=pr_url,
                error=str(e),
            )
            raise GitHubAPIError(f"Failed to fetch PR diff: {e}", error_code=E5002_GITHUB_API_ERROR)
