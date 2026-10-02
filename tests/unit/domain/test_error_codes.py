"""Tests for error code system.

Tests PRDifferException error code attribute, string formatting,
and proper error code usage across the application.
"""

from prdiffer.domain.exceptions import (
    PRDifferException,
    ValidationError,
    InvalidURLError,
    AuthenticationError,
    InvalidTokenError,
    MissingTokenError,
    RateLimitError,
    GitHubAPIError,
    RepositoryNotFoundError,
    PRNotFoundError,
    FileNotFoundError,
    CacheError,
    ConfigurationError,
    ProcessingError,
    ResourceError,
    SecurityError,
)
from prdiffer.domain.error_codes import (
    E1001_INVALID_URL,
    E1002_INVALID_REPOSITORY,
    E1003_INVALID_PR_NUMBER,
    E1004_SUSPICIOUS_INPUT,
    E1005_INPUT_TOO_LONG,
    E1006_INVALID_PATTERN,
    E2001_AUTH_REQUIRED,
    E2002_AUTH_FAILED,
    E2003_INSUFFICIENT_PERMISSIONS,
    E2004_EXPIRED_TOKEN,
    E2005_GITHUB_AUTH_FAILED,
    E2006_GITLAB_AUTH_FAILED,
    E2007_GITLAB_INSUFFICIENT_PERMISSIONS,
    E3001_RATE_LIMITED,
    E3002_SECONDARY_RATE_LIMIT,
    E3003_ABUSE_DETECTION,
    E3004_GLOBAL_RATE_LIMIT,
    E3005_USER_RATE_LIMIT,
    E3006_GITLAB_RATE_LIMITED,
    E4001_REPO_NOT_FOUND,
    E4002_PR_NOT_FOUND,
    E4003_FILE_NOT_FOUND,
    E4004_BRANCH_NOT_FOUND,
    E5001_INTERNAL_ERROR,
    E5002_GITHUB_API_ERROR,
    E5003_DIFF_GENERATION_ERROR,
    E5004_TIMEOUT_ERROR,
    E5005_CIRCUIT_OPEN,
    E5006_CACHE_ERROR,
    E5007_CACHE_INVALIDATION_ERROR,
    E5008_CACHE_CORRUPTION_ERROR,
    E5009_CONFIGURATION_ERROR,
    E5010_MISSING_CONFIGURATION,
    E5011_SECRETS_ERROR,
    E5012_FILE_PROCESSING_ERROR,
    E5013_PATTERN_MATCHING_ERROR,
    E5014_RESOURCE_EXHAUSTED,
    E5015_MEMORY_LIMIT,
    E5016_SUSPICIOUS_OPERATION,
    E5017_INPUT_SANITIZATION_ERROR,
    E5018_SIGNATURE_VERIFICATION_ERROR,
    E5019_CONNECTION_ERROR,
    E5020_FULL_DIFF_INCOMPLETE,
    E5021_GITLAB_API_ERROR,
)


class TestPRDifferException:
    """Test PRDifferException base class functionality."""

    def test_default_error_code(self):
        """Test that PRDifferException has default error code."""
        exc = PRDifferException("Test error")
        assert exc.error_code == E5001_INTERNAL_ERROR
        assert exc.error_code.code == "E5001"
        assert exc.message == "Test error"

    def test_custom_error_code(self):
        """Test that PRDifferException accepts custom error code."""
        exc = PRDifferException("Test error", error_code=E1001_INVALID_URL)
        assert exc.error_code == E1001_INVALID_URL
        assert exc.error_code.code == "E1001"

    def test_str_formatting_with_error_code(self):
        """Test that __str__ formats error code correctly."""
        exc = PRDifferException("Test error", error_code=E1001_INVALID_URL)
        assert str(exc) == "[E1001] Test error"

    def test_str_formatting_without_error_code(self):
        """Test that __str__ handles None error code (defaults to E5001)."""
        exc = PRDifferException("Test error", error_code=None)
        assert str(exc) == f"[{E5001_INTERNAL_ERROR.code}] Test error"

    def test_details_dict(self):
        """Test that details dictionary is stored correctly."""
        exc = PRDifferException("Test error", details={"key": "value"})
        assert exc.details == {"key": "value"}

    def test_details_default_to_empty(self):
        """Test that details defaults to empty dict."""
        exc = PRDifferException("Test error")
        assert exc.details == {}

    def test_inheritance(self):
        """Test that PRDifferException is an Exception."""
        exc = PRDifferException("Test error")
        assert isinstance(exc, Exception)


class TestValidationError:
    """Test ValidationError exception with error codes."""

    def test_validation_error_with_error_code(self):
        """Test ValidationError with error code."""
        exc = ValidationError("Invalid input", error_code=E1001_INVALID_URL)
        assert exc.error_code == E1001_INVALID_URL
        assert str(exc) == "[E1001] Invalid input"

    def test_invalid_url_error_with_error_code(self):
        """Test InvalidURLError with error code."""
        exc = InvalidURLError("Bad URL", error_code=E1001_INVALID_URL)
        assert exc.error_code == E1001_INVALID_URL
        assert isinstance(exc, ValidationError)
        assert isinstance(exc, PRDifferException)


class TestAuthenticationError:
    """Test AuthenticationError exception with error codes."""

    def test_authentication_error_with_error_code(self):
        """Test AuthenticationError with error code."""
        exc = AuthenticationError("Auth failed", error_code=E2002_AUTH_FAILED)
        assert exc.error_code == E2002_AUTH_FAILED
        assert str(exc) == "[E2002] Auth failed"

    def test_invalid_token_error_with_error_code(self):
        """Test InvalidTokenError with error code."""
        exc = InvalidTokenError("Bad token", error_code=E2005_GITHUB_AUTH_FAILED)
        assert exc.error_code == E2005_GITHUB_AUTH_FAILED
        assert isinstance(exc, AuthenticationError)

    def test_missing_token_error_with_error_code(self):
        """Test MissingTokenError with error code."""
        exc = MissingTokenError("No token", error_code=E2001_AUTH_REQUIRED)
        assert exc.error_code == E2001_AUTH_REQUIRED
        assert isinstance(exc, AuthenticationError)


class TestRateLimitError:
    """Test RateLimitError exception with error codes."""

    def test_rate_limit_error_with_retry_after(self):
        """Test RateLimitError with retry_after parameter."""
        exc = RateLimitError(
            "Rate limited",
            retry_after=60,
            error_code=E3001_RATE_LIMITED,
        )
        assert exc.error_code == E3001_RATE_LIMITED
        assert exc.retry_after == 60
        assert str(exc) == "[E3001] Rate limited"


class TestGitHubAPIError:
    """Test GitHubAPIError exception with error codes."""

    def test_github_api_error_with_status_code(self):
        """Test GitHubAPIError with status code."""
        exc = GitHubAPIError("API error", status_code=404, error_code=E4001_REPO_NOT_FOUND)
        assert exc.error_code == E4001_REPO_NOT_FOUND
        assert exc.status_code == 404

    def test_repository_not_found_error(self):
        """Test RepositoryNotFoundError with error code."""
        exc = RepositoryNotFoundError("Repo not found", error_code=E4001_REPO_NOT_FOUND)
        assert exc.error_code == E4001_REPO_NOT_FOUND
        assert isinstance(exc, GitHubAPIError)

    def test_pr_not_found_error(self):
        """Test PRNotFoundError with error code."""
        exc = PRNotFoundError("PR not found", error_code=E4002_PR_NOT_FOUND)
        assert exc.error_code == E4002_PR_NOT_FOUND
        assert isinstance(exc, GitHubAPIError)

    def test_file_not_found_error(self):
        """Test FileNotFoundError with error code."""
        exc = FileNotFoundError("File not found", error_code=E4003_FILE_NOT_FOUND)
        assert exc.error_code == E4003_FILE_NOT_FOUND
        assert isinstance(exc, GitHubAPIError)


class TestCacheError:
    """Test CacheError exception with error codes."""

    def test_cache_error_with_error_code(self):
        """Test CacheError with error code."""
        exc = CacheError("Cache error", error_code=E5006_CACHE_ERROR)
        assert exc.error_code == E5006_CACHE_ERROR
        assert str(exc) == "[E5006] Cache error"


class TestConfigurationError:
    """Test ConfigurationError exception with error codes."""

    def test_configuration_error_with_error_code(self):
        """Test ConfigurationError with error code."""
        exc = ConfigurationError("Bad config", error_code=E5009_CONFIGURATION_ERROR)
        assert exc.error_code == E5009_CONFIGURATION_ERROR


class TestProcessingError:
    """Test ProcessingError exception with error codes."""

    def test_processing_error_with_error_code(self):
        """Test ProcessingError with error code."""
        exc = ProcessingError("Processing failed", error_code=E5012_FILE_PROCESSING_ERROR)
        assert exc.error_code == E5012_FILE_PROCESSING_ERROR


class TestResourceError:
    """Test ResourceError exception with error codes."""

    def test_resource_error_with_error_code(self):
        """Test ResourceError with error code."""
        exc = ResourceError("Resource exhausted", error_code=E5014_RESOURCE_EXHAUSTED)
        assert exc.error_code == E5014_RESOURCE_EXHAUSTED


class TestSecurityError:
    """Test SecurityError exception with error codes."""

    def test_security_error_with_error_code(self):
        """Test SecurityError with error code."""
        exc = SecurityError("Security issue", error_code=E5016_SUSPICIOUS_OPERATION)
        assert exc.error_code == E5016_SUSPICIOUS_OPERATION


class TestErrorCodeConstants:
    """Test that error code constants are properly defined."""

    def test_validation_error_codes(self):
        """Test validation error codes (E1xxx)."""
        assert E1001_INVALID_URL.code == "E1001"
        assert E1001_INVALID_URL.category.name == "INPUT_VALIDATION"
        assert E1002_INVALID_REPOSITORY.code == "E1002"
        assert E1003_INVALID_PR_NUMBER.code == "E1003"
        assert E1004_SUSPICIOUS_INPUT.code == "E1004"
        assert E1005_INPUT_TOO_LONG.code == "E1005"
        assert E1006_INVALID_PATTERN.code == "E1006"

    def test_authentication_error_codes(self):
        """Test authentication error codes (E2xxx)."""
        assert E2001_AUTH_REQUIRED.code == "E2001"
        assert E2001_AUTH_REQUIRED.category.name == "AUTHENTICATION"
        assert E2002_AUTH_FAILED.code == "E2002"
        assert E2003_INSUFFICIENT_PERMISSIONS.code == "E2003"
        assert E2004_EXPIRED_TOKEN.code == "E2004"
        assert E2005_GITHUB_AUTH_FAILED.code == "E2005"
        assert E2006_GITLAB_AUTH_FAILED.code == "E2006"
        assert E2007_GITLAB_INSUFFICIENT_PERMISSIONS.code == "E2007"

    def test_rate_limit_error_codes(self):
        """Test rate limit error codes (E3xxx)."""
        assert E3001_RATE_LIMITED.code == "E3001"
        assert E3001_RATE_LIMITED.category.name == "RATE_LIMITING"
        assert E3002_SECONDARY_RATE_LIMIT.code == "E3002"
        assert E3003_ABUSE_DETECTION.code == "E3003"
        assert E3004_GLOBAL_RATE_LIMIT.code == "E3004"
        assert E3005_USER_RATE_LIMIT.code == "E3005"
        assert E3006_GITLAB_RATE_LIMITED.code == "E3006"

    def test_not_found_error_codes(self):
        """Test not found error codes (E4xxx)."""
        assert E4001_REPO_NOT_FOUND.code == "E4001"
        assert E4001_REPO_NOT_FOUND.category.name == "NOT_FOUND"
        assert E4002_PR_NOT_FOUND.code == "E4002"
        assert E4003_FILE_NOT_FOUND.code == "E4003"
        assert E4004_BRANCH_NOT_FOUND.code == "E4004"

    def test_internal_error_codes(self):
        """Test internal server error codes (E5xxx)."""
        assert E5001_INTERNAL_ERROR.code == "E5001"
        assert E5001_INTERNAL_ERROR.category.name == "INTERNAL"
        assert E5002_GITHUB_API_ERROR.code == "E5002"
        assert E5003_DIFF_GENERATION_ERROR.code == "E5003"
        assert E5004_TIMEOUT_ERROR.code == "E5004"
        assert E5005_CIRCUIT_OPEN.code == "E5005"
        assert E5006_CACHE_ERROR.code == "E5006"
        assert E5007_CACHE_INVALIDATION_ERROR.code == "E5007"
        assert E5008_CACHE_CORRUPTION_ERROR.code == "E5008"
        assert E5009_CONFIGURATION_ERROR.code == "E5009"
        assert E5010_MISSING_CONFIGURATION.code == "E5010"
        assert E5011_SECRETS_ERROR.code == "E5011"
        assert E5012_FILE_PROCESSING_ERROR.code == "E5012"
        assert E5013_PATTERN_MATCHING_ERROR.code == "E5013"
        assert E5014_RESOURCE_EXHAUSTED.code == "E5014"
        assert E5015_MEMORY_LIMIT.code == "E5015"
        assert E5016_SUSPICIOUS_OPERATION.code == "E5016"
        assert E5017_INPUT_SANITIZATION_ERROR.code == "E5017"
        assert E5018_SIGNATURE_VERIFICATION_ERROR.code == "E5018"
        assert E5019_CONNECTION_ERROR.code == "E5019"
        assert E5020_FULL_DIFF_INCOMPLETE.code == "E5020"
        assert E5021_GITLAB_API_ERROR.code == "E5021"

    def test_gitlab_error_codes_messages(self):
        """GitLab operational codes have provider-specific messages/remediation."""
        assert "GitLab" in E2006_GITLAB_AUTH_FAILED.message
        assert "token" in E2006_GITLAB_AUTH_FAILED.remediation.lower() or "GITLAB" in E2006_GITLAB_AUTH_FAILED.remediation
        assert "permission" in E2007_GITLAB_INSUFFICIENT_PERMISSIONS.message.lower()
        assert "rate limit" in E3006_GITLAB_RATE_LIMITED.message.lower()
        assert E5021_GITLAB_API_ERROR.category.name == "INTERNAL"

    def test_all_error_code_constants_unique(self):
        import prdiffer.domain.error_codes as codes

        all_codes = [value.code for name, value in vars(codes).items() if name.startswith("E") and hasattr(value, "code")]
        assert len(all_codes) == len(set(all_codes))
        assert "E2006" in all_codes
        assert "E2007" in all_codes
        assert "E3006" in all_codes
        assert "E5021" in all_codes

    def test_error_code_properties(self):
        """Test that error codes have all required properties."""
        error_codes = [
            E1001_INVALID_URL,
            E2002_AUTH_FAILED,
            E3001_RATE_LIMITED,
            E4001_REPO_NOT_FOUND,
            E5001_INTERNAL_ERROR,
        ]

        for error_code in error_codes:
            assert hasattr(error_code, "code")
            assert hasattr(error_code, "name")
            assert hasattr(error_code, "message")
            assert hasattr(error_code, "remediation")
            assert hasattr(error_code, "category")
            assert error_code.code.startswith("E")
            assert error_code.message
            assert error_code.remediation

    def test_error_code_to_dict(self):
        """Test that error codes can be converted to dict."""
        error_dict = E1001_INVALID_URL.to_dict()
        assert error_dict["error_code"] == "E1001_INVALID_URL"
        assert error_dict["message"] == E1001_INVALID_URL.message
        assert error_dict["remediation"] == E1001_INVALID_URL.remediation
        assert error_dict["category"] == "INPUT_VALIDATION"

    def test_error_code_str(self):
        """Test that error codes have proper string representation."""
        assert str(E1001_INVALID_URL) == "E1001_INVALID_URL"
        assert str(E2002_AUTH_FAILED) == "E2002_AUTH_FAILED"


class TestErrorCategories:
    """Test error categories and classification."""

    def test_all_error_codes_have_categories(self):
        """Test that all error codes have assigned categories."""
        error_codes = [
            E1001_INVALID_URL,
            E1002_INVALID_REPOSITORY,
            E1003_INVALID_PR_NUMBER,
            E1004_SUSPICIOUS_INPUT,
            E1005_INPUT_TOO_LONG,
            E1006_INVALID_PATTERN,
            E2001_AUTH_REQUIRED,
            E2002_AUTH_FAILED,
            E2003_INSUFFICIENT_PERMISSIONS,
            E2004_EXPIRED_TOKEN,
            E2005_GITHUB_AUTH_FAILED,
            E2006_GITLAB_AUTH_FAILED,
            E2007_GITLAB_INSUFFICIENT_PERMISSIONS,
            E3001_RATE_LIMITED,
            E3002_SECONDARY_RATE_LIMIT,
            E3003_ABUSE_DETECTION,
            E3004_GLOBAL_RATE_LIMIT,
            E3005_USER_RATE_LIMIT,
            E3006_GITLAB_RATE_LIMITED,
            E4001_REPO_NOT_FOUND,
            E4002_PR_NOT_FOUND,
            E4003_FILE_NOT_FOUND,
            E4004_BRANCH_NOT_FOUND,
            E5001_INTERNAL_ERROR,
            E5002_GITHUB_API_ERROR,
            E5003_DIFF_GENERATION_ERROR,
            E5004_TIMEOUT_ERROR,
            E5005_CIRCUIT_OPEN,
            E5006_CACHE_ERROR,
            E5007_CACHE_INVALIDATION_ERROR,
            E5008_CACHE_CORRUPTION_ERROR,
            E5009_CONFIGURATION_ERROR,
            E5010_MISSING_CONFIGURATION,
            E5011_SECRETS_ERROR,
            E5012_FILE_PROCESSING_ERROR,
            E5013_PATTERN_MATCHING_ERROR,
            E5014_RESOURCE_EXHAUSTED,
            E5015_MEMORY_LIMIT,
            E5016_SUSPICIOUS_OPERATION,
            E5017_INPUT_SANITIZATION_ERROR,
            E5018_SIGNATURE_VERIFICATION_ERROR,
            E5019_CONNECTION_ERROR,
            E5020_FULL_DIFF_INCOMPLETE,
            E5021_GITLAB_API_ERROR,
        ]

        for error_code in error_codes:
            assert error_code.category.name in [
                "INPUT_VALIDATION",
                "AUTHENTICATION",
                "RATE_LIMITING",
                "NOT_FOUND",
                "INTERNAL",
            ]

    def test_error_code_numbering(self):
        """Test that error codes follow numbering convention."""
        assert E1001_INVALID_URL.code.startswith("E1")
        assert E2001_AUTH_REQUIRED.code.startswith("E2")
        assert E3001_RATE_LIMITED.code.startswith("E3")
        assert E4001_REPO_NOT_FOUND.code.startswith("E4")
        assert E5001_INTERNAL_ERROR.code.startswith("E5")
