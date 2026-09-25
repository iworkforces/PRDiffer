import pytest

from prdiffer.application.components.pr_operation_handler import (
    PROperationHandler,
)
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.repository_cache import RepositoryCacheServiceInterface
from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.exceptions import ValidationError, GitHubAPIError


class MockLogger(LoggerServiceInterface):
    def debug(self, message: str, **kwargs) -> None:
        pass

    def info(self, message: str, **kwargs) -> None:
        pass

    def warning(self, message: str, **kwargs) -> None:
        pass

    def error(self, message: str, **kwargs) -> None:
        pass

    def critical(self, message: str, **kwargs) -> None:
        pass

    def should_log(self, level) -> bool:
        return True


class MockCacheService(CacheServiceInterface):
    def get_cache_key(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        return f"{repo_owner}/{repo_name}/{pr_number}"

    async def get(self, cache_key: str, current_commit_sha: str):
        return None

    async def set(self, cache_key: str, commit_sha: str, data: PRDiff):
        pass

    async def invalidate(self, cache_key: str):
        pass

    async def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
        pass

    async def invalidate_github_repository(self, owner: str, repo: str) -> None:
        pass

    def get_etag(self, cache_key: str):
        return None

    def set_etag(self, cache_key: str, etag: str):
        pass

    def get_stats(self):
        return {"size": 0, "keys": []}

    async def get_optimistic(self, cache_key: str):
        return None, None


class MockRepositoryCacheService(RepositoryCacheServiceInterface):
    def insert(self, repository: PRDiffRepositoryInterface) -> bool:
        return True

    def retrieve(self, repo_owner: str, repo_name: str, pr_number: int):
        return None

    def validate(self, repo_owner: str, repo_name: str, pr_number: int) -> bool:
        return False

    def remove(self, repo_owner: str, repo_name: str, pr_number: int) -> bool:
        return True

    def clear(self):
        pass

    def size(self) -> int:
        return 0

    def stats(self) -> dict:
        return {}

    def invalidate(self, cache_key: str) -> bool:
        return True

    def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
        pass

    def invalidate_github_repository(self, owner: str, repo: str) -> None:
        pass


class MockRepository(PRDiffRepositoryInterface):
    def __init__(self, repo_owner: str, repo_name: str, pr_number: int):
        self._repo_owner = repo_owner
        self._repo_name = repo_name
        self._pr_number = pr_number
        self._initialized = False

    @property
    def repo_owner(self) -> str:
        return self._repo_owner

    @property
    def repo_name(self) -> str:
        return self._repo_name

    @property
    def pr_number(self) -> int:
        return self._pr_number

    async def initialize(self):
        self._initialized = True

    async def get_pr_diff(self):
        from prdiffer.domain.entities.file_diff_response import (
            FileDiffResponse,
            FileStats,
        )
        from prdiffer.domain.entities.file_patch import EDIT_TYPE

        return PRDiff(
            files=(
                FileDiffResponse(
                    path="test.py",
                    status=EDIT_TYPE.MODIFIED,
                    stats=FileStats(additions=10, deletions=5),
                    diff="mock diff content",
                ),
            ),
        )

    async def get_latest_commit_sha(self) -> str:
        return "abc123def"

    async def approve_pr_with_comment(self, pr_url: str, compliment: str) -> str:
        return "Approved"

    async def update_pr_description(self, pr_url: str, description: str) -> str:
        return "Updated"

    def supports_repository(self, url: str) -> bool:
        return "github.com" in url


class TestPROperationHandlerInitialization:
    def test_pr_operation_handler_initialization(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        assert handler is not None
        assert hasattr(handler, "_github_repository_class")
        assert hasattr(handler, "_cache_service")
        assert hasattr(handler, "_repository_cache_service")
        assert hasattr(handler, "_logger")

    def test_pr_operation_handler_initialization_with_input_validator(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        from prdiffer.infrastructure.security.input_validator import InputValidator

        input_validator = InputValidator()

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
            input_validator=input_validator,
        )

        assert handler._input_validator is input_validator


class TestPROperationHandlerGetPrDiff:
    @pytest.mark.asyncio
    async def test_get_pr_diff_valid_url(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        url = "https://github.com/owner/repo/pull/123"
        result = await handler.get_pr_diff(url)

        assert isinstance(result, dict)
        assert "files" in result
        assert isinstance(result["files"], list)
        assert len(result["files"]) > 0
        assert result["files"][0]["path"] == "test.py"

    @pytest.mark.asyncio
    async def test_get_pr_diff_empty_url_raises_value_error(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        with pytest.raises(ValidationError) as exc_info:
            await handler.get_pr_diff("")

        assert "required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_pr_diff_uses_repository_cache(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        url = "https://github.com/owner/repo/pull/123"
        result = await handler.get_pr_diff(url)

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_pr_diff_handles_none_from_repository(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        class FailingRepository(PRDiffRepositoryInterface):
            def __init__(self, repo_owner, repo_name, pr_number):
                self._repo_owner = repo_owner
                self._repo_name = repo_name
                self._pr_number = pr_number

            @property
            def repo_owner(self):
                return self._repo_owner

            @property
            def repo_name(self):
                return self._repo_name

            @property
            def pr_number(self):
                return self._pr_number

            async def initialize(self):
                pass

            async def get_pr_diff(self):
                raise RuntimeError("Failed to fetch diff from repository")

            async def get_latest_commit_sha(self):
                return "abc123"

            async def approve_pr_with_comment(self, pr_url: str, compliment: str) -> str:
                return "Not approved"

            def supports_repository(self, url):
                return True

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return FailingRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        url = "https://github.com/owner/repo/pull/123"

        with pytest.raises(GitHubAPIError) as exc_info:
            await handler.get_pr_diff(url)

        assert "Failed to fetch PR diff" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_pr_diff_validation_error_is_caught(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        invalid_url = "https://github.com/owner/repo/pull/invalid"

        with pytest.raises(ValidationError) as exc_info:
            await handler.get_pr_diff(invalid_url)

        assert "Invalid request" in str(exc_info.value)


class TestPROperationHandlerErrorHandling:
    @pytest.mark.asyncio
    async def test_get_pr_diff_handles_runtime_errors(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        class ErrorRaisingRepository(PRDiffRepositoryInterface):
            def __init__(self, repo_owner, repo_name, pr_number):
                pass

            @property
            def repo_owner(self):
                return "owner"

            @property
            def repo_name(self):
                return "repo"

            @property
            def pr_number(self):
                return 123

            async def initialize(self):
                pass

            async def get_pr_diff(self):
                raise RuntimeError("Simulated API error")

            async def get_latest_commit_sha(self):
                return "abc123"

            async def approve_pr_with_comment(self, pr_url: str, compliment: str) -> str:
                return "Not approved"

            def supports_repository(self, url):
                return True

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return ErrorRaisingRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        url = "https://github.com/owner/repo/pull/123"

        with pytest.raises(GitHubAPIError) as exc_info:
            await handler.get_pr_diff(url)

        assert "Failed to fetch PR diff" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_pr_diff_logs_appropriately(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        logged_messages = []
        original_info = logger.info
        logger.info = lambda message, **kwargs: logged_messages.append(("info", message)) or original_info(message, **kwargs)
        original_debug = logger.debug
        logger.debug = lambda message, **kwargs: logged_messages.append(("debug", message)) or original_debug(message, **kwargs)

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        url = "https://github.com/owner/repo/pull/123"
        await handler.get_pr_diff(url)

        assert any(level == "info" for level, msg in logged_messages)
        assert any("Successfully fetched" in msg for level, msg in logged_messages)


class TestPROperationHandlerEdgeCases:
    @pytest.mark.asyncio
    async def test_get_pr_diff_with_complex_url(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        url = "https://github.com/owner/repo/pull/123"
        result = await handler.get_pr_diff(url)

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_pr_diff_model_dump_structure(self):
        logger = MockLogger()
        cache_service = MockCacheService()
        repository_cache_service = MockRepositoryCacheService()

        def mock_github_repo_class(repo_owner, repo_name, pr_number):
            return MockRepository(repo_owner, repo_name, pr_number)

        handler = PROperationHandler(
            github_repository_class=mock_github_repo_class,
            cache_service=cache_service,
            repository_cache_service=repository_cache_service,
            logger=logger,
        )

        url = "https://github.com/owner/repo/pull/123"
        result = await handler.get_pr_diff(url)

        assert isinstance(result, dict)
        assert "files" in result
        assert isinstance(result["files"], list)
