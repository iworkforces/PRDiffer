"""Error code constant definitions for PRDiffer.

This module contains all E*_ error code constant definitions.
Types and utilities are in errors.py.

Error Categories:
- E1xxx: Input validation errors
- E2xxx: Authentication/authorization errors
- E3xxx: Rate limiting errors
- E4xxx: Resource not found errors
- E5xxx: Internal server errors
"""

from prdiffer.domain.errors import ErrorCode, ErrorCategory

# =============================================================================
# Input Validation Errors (E1xxx)
# =============================================================================

E1001_INVALID_URL = ErrorCode(
    code="E1001",
    name="INVALID_URL",
    message="Malformed GitHub PR URL",
    remediation="Provide a valid GitHub PR URL in format: https://github.com/owner/repo/pull/123",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1002_INVALID_REPOSITORY = ErrorCode(
    code="E1002",
    name="INVALID_REPOSITORY",
    message="Invalid repository identifier",
    remediation="Repository owner and name must be alphanumeric with hyphens/underscores",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1003_INVALID_PR_NUMBER = ErrorCode(
    code="E1003",
    name="INVALID_PR_NUMBER",
    message="Invalid pull request number",
    remediation="PR number must be a positive integer",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1004_SUSPICIOUS_INPUT = ErrorCode(
    code="E1004",
    name="SUSPICIOUS_INPUT",
    message="Request contains suspicious patterns",
    remediation="Remove any command injection, SQL, or path traversal patterns",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1005_INPUT_TOO_LONG = ErrorCode(
    code="E1005",
    name="INPUT_TOO_LONG",
    message="Input exceeds maximum allowed length",
    remediation="Reduce input length to within limits",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1006_INVALID_PATTERN = ErrorCode(
    code="E1006",
    name="INVALID_PATTERN",
    message="Invalid file pattern provided",
    remediation="Use valid glob patterns (e.g., '*.py', 'src/**/*.ts')",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1007_INVALID_TOKEN = ErrorCode(
    code="E1007",
    name="INVALID_TOKEN",
    message="Invalid or malformed authentication token",
    remediation="Provide a valid token with proper format and permissions",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1008_MISSING_TOKEN = ErrorCode(
    code="E1008",
    name="MISSING_TOKEN",
    message="Authentication token not provided",
    remediation="Provide authentication token via GITHUB_TOKEN environment variable",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1009_INVALID_FORMAT = ErrorCode(
    code="E1009",
    name="INVALID_FORMAT",
    message="Data format not supported",
    remediation="Ensure data is in supported format (e.g., valid JSON, text)",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1010_INVALID_CONFIGURATION = ErrorCode(
    code="E1010",
    name="INVALID_CONFIGURATION",
    message="Configuration value is invalid",
    remediation="Check configuration values in settings.toml or environment variables",
    category=ErrorCategory.INPUT_VALIDATION,
)

E1011_HEAD_SHA_MISMATCH = ErrorCode(
    code="E1011",
    name="HEAD_SHA_MISMATCH",
    message="Pull request head no longer matches the reviewed commit",
    remediation="Call get_pr_diff again, review the new head, and approve with its head_sha",
    category=ErrorCategory.INPUT_VALIDATION,
)

# =============================================================================
# Authentication/Authorization Errors (E2xxx)
# =============================================================================

E2001_AUTH_REQUIRED = ErrorCode(
    code="E2001",
    name="AUTH_REQUIRED",
    message="GitHub authentication required",
    remediation="Provide a valid GitHub token via GITHUB_TOKEN environment variable",
    category=ErrorCategory.AUTHENTICATION,
)

E2002_AUTH_FAILED = ErrorCode(
    code="E2002",
    name="AUTH_FAILED",
    message="GitHub authentication failed",
    remediation="Verify your GitHub token is valid and has required permissions",
    category=ErrorCategory.AUTHENTICATION,
)

E2003_INSUFFICIENT_PERMISSIONS = ErrorCode(
    code="E2003",
    name="INSUFFICIENT_PERMISSIONS",
    message="Insufficient permissions to access resource",
    remediation="Ensure token has 'repo' scope for private repositories",
    category=ErrorCategory.AUTHENTICATION,
)

E2004_EXPIRED_TOKEN = ErrorCode(
    code="E2004",
    name="EXPIRED_TOKEN",
    message="Authentication token has expired",
    remediation="Generate a new token from your GitHub settings",
    category=ErrorCategory.AUTHENTICATION,
)

E2005_GITHUB_AUTH_FAILED = ErrorCode(
    code="E2005",
    name="GITHUB_AUTH_FAILED",
    message="GitHub authentication failed",
    remediation="Verify token is valid and has required permissions",
    category=ErrorCategory.AUTHENTICATION,
)

E2006_GITLAB_AUTH_FAILED = ErrorCode(
    code="E2006",
    name="GITLAB_AUTH_FAILED",
    message="GitLab authentication failed",
    remediation="Verify GITLAB_TOKEN is valid and has required scopes",
    category=ErrorCategory.AUTHENTICATION,
)

E2007_GITLAB_INSUFFICIENT_PERMISSIONS = ErrorCode(
    code="E2007",
    name="GITLAB_INSUFFICIENT_PERMISSIONS",
    message="Insufficient permissions for GitLab resource",
    remediation="Ensure the token can read the project and merge request",
    category=ErrorCategory.AUTHENTICATION,
)

# =============================================================================
# Rate Limiting Errors (E3xxx)
# =============================================================================

E3001_RATE_LIMITED = ErrorCode(
    code="E3001",
    name="RATE_LIMITED",
    message="GitHub API rate limit exceeded",
    remediation="Wait for rate limit reset or use authenticated requests for higher limits",
    category=ErrorCategory.RATE_LIMITING,
)

E3002_SECONDARY_RATE_LIMIT = ErrorCode(
    code="E3002",
    name="SECONDARY_RATE_LIMIT",
    message="GitHub secondary rate limit triggered",
    remediation="Reduce request frequency and wait before retrying",
    category=ErrorCategory.RATE_LIMITING,
)

E3003_ABUSE_DETECTION = ErrorCode(
    code="E3003",
    name="ABUSE_DETECTION",
    message="GitHub abuse detection triggered",
    remediation="Reduce request frequency significantly and contact GitHub if persistent",
    category=ErrorCategory.RATE_LIMITING,
)

E3004_GLOBAL_RATE_LIMIT = ErrorCode(
    code="E3004",
    name="GLOBAL_RATE_LIMIT",
    message="Global server rate limit exceeded",
    remediation="Reduce request frequency across all operations",
    category=ErrorCategory.RATE_LIMITING,
)

E3005_USER_RATE_LIMIT = ErrorCode(
    code="E3005",
    name="USER_RATE_LIMIT",
    message="Per-user rate limit exceeded",
    remediation="Wait for rate limit reset or use authenticated requests",
    category=ErrorCategory.RATE_LIMITING,
)

E3006_GITLAB_RATE_LIMITED = ErrorCode(
    code="E3006",
    name="GITLAB_RATE_LIMITED",
    message="GitLab API rate limit exceeded",
    remediation="Wait for rate limit reset or reduce request frequency",
    category=ErrorCategory.RATE_LIMITING,
)

# =============================================================================
# Resource Not Found Errors (E4xxx)
# =============================================================================

E4001_REPO_NOT_FOUND = ErrorCode(
    code="E4001",
    name="REPO_NOT_FOUND",
    message="Repository not found",
    remediation="Verify repository owner and name are correct, check access permissions",
    category=ErrorCategory.NOT_FOUND,
)

E4002_PR_NOT_FOUND = ErrorCode(
    code="E4002",
    name="PR_NOT_FOUND",
    message="Pull request not found",
    remediation="Verify PR number exists in the repository",
    category=ErrorCategory.NOT_FOUND,
)

E4003_FILE_NOT_FOUND = ErrorCode(
    code="E4003",
    name="FILE_NOT_FOUND",
    message="File not found in repository",
    remediation="Verify file path and commit reference are correct",
    category=ErrorCategory.NOT_FOUND,
)

E4004_BRANCH_NOT_FOUND = ErrorCode(
    code="E4004",
    name="BRANCH_NOT_FOUND",
    message="Branch or commit not found",
    remediation="Verify branch name or commit SHA exists",
    category=ErrorCategory.NOT_FOUND,
)

# =============================================================================
# Internal Server Errors (E5xxx)
# =============================================================================

E5001_INTERNAL_ERROR = ErrorCode(
    code="E5001",
    name="INTERNAL_ERROR",
    message="Internal server error",
    remediation="Retry the request; contact support if issue persists",
    category=ErrorCategory.INTERNAL,
)

E5002_GITHUB_API_ERROR = ErrorCode(
    code="E5002",
    name="GITHUB_API_ERROR",
    message="GitHub API error occurred",
    remediation="Retry the request; GitHub may be experiencing issues",
    category=ErrorCategory.INTERNAL,
)

E5003_DIFF_GENERATION_ERROR = ErrorCode(
    code="E5003",
    name="DIFF_GENERATION_ERROR",
    message="Failed to generate diff",
    remediation="The diff may be too large; try with smaller file selection",
    category=ErrorCategory.INTERNAL,
)

E5004_TIMEOUT_ERROR = ErrorCode(
    code="E5004",
    name="TIMEOUT_ERROR",
    message="Request timed out",
    remediation="Retry with smaller file selection or increased timeout",
    category=ErrorCategory.INTERNAL,
)

E5005_CIRCUIT_OPEN = ErrorCode(
    code="E5005",
    name="CIRCUIT_OPEN",
    message="Service temporarily unavailable",
    remediation="Wait a moment and retry; circuit breaker is active",
    category=ErrorCategory.INTERNAL,
)

E5006_CACHE_ERROR = ErrorCode(
    code="E5006",
    name="CACHE_ERROR",
    message="Cache operation failed",
    remediation="Cache error occurred; operation will be retried without cache",
    category=ErrorCategory.INTERNAL,
)

E5007_CACHE_INVALIDATION_ERROR = ErrorCode(
    code="E5007",
    name="CACHE_INVALIDATION_ERROR",
    message="Cache invalidation failed",
    remediation="Manual cache clear may be required",
    category=ErrorCategory.INTERNAL,
)

E5008_CACHE_CORRUPTION_ERROR = ErrorCode(
    code="E5008",
    name="CACHE_CORRUPTION_ERROR",
    message="Cached data is corrupted",
    remediation="Clear cache and retry operation",
    category=ErrorCategory.INTERNAL,
)

E5009_CONFIGURATION_ERROR = ErrorCode(
    code="E5009",
    name="CONFIGURATION_ERROR",
    message="Configuration is invalid or missing",
    remediation="Check configuration in settings.toml or environment variables",
    category=ErrorCategory.INTERNAL,
)

E5010_MISSING_CONFIGURATION = ErrorCode(
    code="E5010",
    name="MISSING_CONFIGURATION",
    message="Required configuration is missing",
    remediation="Provide required configuration values",
    category=ErrorCategory.INTERNAL,
)

E5011_SECRETS_ERROR = ErrorCode(
    code="E5011",
    name="SECRETS_ERROR",
    message="Secrets management failed",
    remediation="Check secret storage and access permissions",
    category=ErrorCategory.INTERNAL,
)

E5012_FILE_PROCESSING_ERROR = ErrorCode(
    code="E5012",
    name="FILE_PROCESSING_ERROR",
    message="File processing failed",
    remediation="Verify file exists and is readable",
    category=ErrorCategory.INTERNAL,
)

E5013_PATTERN_MATCHING_ERROR = ErrorCode(
    code="E5013",
    name="PATTERN_MATCHING_ERROR",
    message="Pattern matching failed",
    remediation="Verify pattern syntax and data format",
    category=ErrorCategory.INTERNAL,
)

E5014_RESOURCE_EXHAUSTED = ErrorCode(
    code="E5014",
    name="RESOURCE_EXHAUSTED",
    message="System resources are exhausted",
    remediation="Reduce concurrent operations or increase system resources",
    category=ErrorCategory.INTERNAL,
)

E5015_MEMORY_LIMIT = ErrorCode(
    code="E5015",
    name="MEMORY_LIMIT",
    message="Memory limit exceeded",
    remediation="Reduce file selection or increase available memory",
    category=ErrorCategory.INTERNAL,
)

E5016_SUSPICIOUS_OPERATION = ErrorCode(
    code="E5016",
    name="SUSPICIOUS_OPERATION",
    message="Suspicious activity detected",
    remediation="Request has been blocked for security reasons",
    category=ErrorCategory.INTERNAL,
)

E5017_INPUT_SANITIZATION_ERROR = ErrorCode(
    code="E5017",
    name="INPUT_SANITIZATION_ERROR",
    message="Input contains potentially malicious content",
    remediation="Remove any injection patterns from input",
    category=ErrorCategory.INTERNAL,
)

E5018_SIGNATURE_VERIFICATION_ERROR = ErrorCode(
    code="E5018",
    name="SIGNATURE_VERIFICATION_ERROR",
    message="Request signature verification failed",
    remediation="Verify request signature and webhook secret",
    category=ErrorCategory.INTERNAL,
)

E5019_CONNECTION_ERROR = ErrorCode(
    code="E5019",
    name="CONNECTION_ERROR",
    message="Connection to service failed",
    remediation="Check network connectivity and service availability",
    category=ErrorCategory.INTERNAL,
)

E5020_FULL_DIFF_INCOMPLETE = ErrorCode(
    code="E5020",
    name="FULL_DIFF_INCOMPLETE",
    message="Selected PR files cannot be returned as a complete full-context diff",
    remediation=("Reduce selected file count/size, ensure provider inventory is complete, and exclude binary or undecodable selected files"),
    category=ErrorCategory.INTERNAL,
)

E5022_PROVIDER_CAPABILITY_UNAVAILABLE = ErrorCode(
    code="E5022",
    name="PROVIDER_CAPABILITY_UNAVAILABLE",
    message="The requested operation is not available for this provider",
    remediation="Use a provider that supports the requested operation",
    category=ErrorCategory.INTERNAL,
)

E5021_GITLAB_API_ERROR = ErrorCode(
    code="E5021",
    name="GITLAB_API_ERROR",
    message="GitLab API error occurred",
    remediation="Retry the request; GitLab may be experiencing issues",
    category=ErrorCategory.INTERNAL,
)
