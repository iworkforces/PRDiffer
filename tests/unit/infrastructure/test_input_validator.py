"""Unit tests for InputValidator security component."""

from collections.abc import Callable

import pytest
from typing import cast, Any
from prdiffer.infrastructure.security.injection_detector import _detector
from prdiffer.infrastructure.security.input_validation_helpers import (
    sanitize_string,
    validate_github_url,
    validate_repository_identifier,
    validate_token,
    validate_user_id,
)
from prdiffer.infrastructure.security.input_validator import InputValidator
from prdiffer.domain.exceptions import (
    InvalidURLError,
    InvalidRepositoryError,
    InvalidPRNumberError,
    InputSanitizationError,
    SuspiciousOperationError,
)


@pytest.fixture()
def validator() -> InputValidator:
    return InputValidator()


@pytest.mark.unit
class TestGitHubURLValidation:
    def test_validate_valid_github_url(self, validator: InputValidator):
        owner, repo, pr_number = validator.validate_github_url("https://github.com/anthropics/claude-code/pull/123")
        assert owner == "anthropics"
        assert repo == "claude-code"
        assert pr_number == 123

    def test_validate_github_url_with_trailing_slash(self, validator: InputValidator):
        owner, repo, pr_number = validator.validate_github_url("https://github.com/owner/repo/pull/456/")
        assert owner == "owner"
        assert repo == "repo"
        assert pr_number == 456

    @pytest.mark.parametrize(
        "invalid_url,expected_exception",
        [
            (
                "http://github.com/owner/repo/pull/123",
                InvalidURLError,
            ),  # HTTP not HTTPS
            ("https://gitlab.com/owner/repo/pull/123", InvalidURLError),  # Not GitHub
            ("https://github.com/owner/pull/123", InvalidURLError),  # Missing repo
            ("https://github.com/owner/repo/123", InvalidURLError),  # Missing 'pull'
            (
                "https://github.com/owner/repo/pull/0",
                InvalidPRNumberError,
            ),  # Invalid PR number
            (
                "https://github.com/owner/repo/pull/1000001",
                InvalidPRNumberError,
            ),  # PR number too large
        ],
    )
    def test_validate_invalid_github_urls(self, validator: InputValidator, invalid_url, expected_exception):
        with pytest.raises(expected_exception):
            validator.validate_github_url(invalid_url)

    def test_validate_github_url_empty(self, validator: InputValidator):
        with pytest.raises(InvalidURLError, match="URL cannot be empty"):
            validator.validate_github_url("")

    def test_validate_github_url_too_long(self, validator: InputValidator):
        long_url = "https://github.com/" + "a" * 3000 + "/repo/pull/123"
        with pytest.raises(InvalidURLError, match="URL too long"):
            validator.validate_github_url(long_url)

    def test_validate_github_url_command_injection(self, validator: InputValidator):
        malicious_urls = [
            "https://github.com/owner/repo/pull/123; rm -rf /",
            "https://github.com/owner/repo/pull/123 && cat /etc/passwd",
            "https://github.com/owner/repo/pull/123$(whoami)",
            "https://github.com/owner/repo/pull/123`ls -la`",
        ]
        for url in malicious_urls:
            with pytest.raises(SuspiciousOperationError):
                validator.validate_github_url(url)

    def test_validate_github_url_path_traversal(self, validator: InputValidator):
        with pytest.raises(SuspiciousOperationError):
            validator.validate_github_url("https://github.com/../../etc/passwd/pull/123")

    def test_validate_github_url_sql_injection(self, validator: InputValidator):
        # Note: SQL injection patterns are now detected before URL parsing
        with pytest.raises(SuspiciousOperationError):
            validator.validate_github_url("https://github.com/owner/repo' OR '1'='1/pull/123")

    def test_validate_github_url_convenience_function(self):
        owner, repo, pr_number = validate_github_url("https://github.com/test/repo/pull/789")
        assert owner == "test"
        assert repo == "repo"
        assert pr_number == 789


@pytest.mark.unit
class TestGitLabURLValidation:
    def test_validate_gitlab_merge_request_url(self, validator: InputValidator) -> None:
        # Given
        url = "https://gitlab.com/owner/repo/-/merge_requests/17"
        validate_gitlab_url: Callable[[str], tuple[str, str, int]] = getattr(validator, "validate_gitlab_url")

        # When
        owner, repo, merge_request_number = validate_gitlab_url(url)

        # Then
        assert (owner, repo, merge_request_number) == ("owner", "repo", 17)

    def test_validate_nested_namespace_gitlab_url(self, validator: InputValidator) -> None:
        url = "https://gitlab.com/group/subgroup/project/-/merge_requests/42"
        validate_gitlab_url: Callable[[str], tuple[str, str, int]] = getattr(validator, "validate_gitlab_url")
        owner, repo, iid = validate_gitlab_url(url)
        assert (owner, repo, iid) == ("group/subgroup", "project", 42)

    def test_validate_custom_hosted_gitlab_url(self, validator: InputValidator) -> None:
        url = "https://gitlab.example.com/trace-analysis/oh-my-grokbuild/-/merge_requests/1"
        validate_gitlab_url: Callable[[str], tuple[str, str, int]] = getattr(validator, "validate_gitlab_url")
        owner, repo, iid = validate_gitlab_url(url)
        assert (owner, repo, iid) == ("trace-analysis", "oh-my-grokbuild", 1)

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://gitlab.com/group/project/-/merge_requests/1",
            "https://gitlab.com/a/b/-/merge_requests/1?foo=1",
            "https://gitlab.com/a/b/-/merge_requests/1#x",
            "https://gitlab.com/a/../b/-/merge_requests/1",
            "https://gitlab.com/a//b/-/merge_requests/1",
        ],
    )
    def test_reject_malformed_gitlab_urls(self, validator: InputValidator, bad_url: str) -> None:
        from prdiffer.domain.exceptions import InvalidPRNumberError, InvalidURLError, SuspiciousOperationError

        validate_gitlab_url: Callable[[str], tuple[str, str, int]] = getattr(validator, "validate_gitlab_url")
        with pytest.raises((InvalidURLError, InvalidPRNumberError, SuspiciousOperationError)):
            validate_gitlab_url(bad_url)


@pytest.mark.unit
class TestRepositoryIdentifierValidation:
    def test_validate_valid_repository_identifier(self):
        owner, repo = InputValidator.validate_repository_identifier("owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_validate_repository_identifier_with_dots(self):
        owner, repo = InputValidator.validate_repository_identifier("owner/repo.name")
        assert owner == "owner"
        assert repo == "repo.name"

    @pytest.mark.parametrize(
        "invalid_identifier",
        [
            "",  # Empty
            "owner",  # Missing slash
            "owner/",  # Missing repo
            "/repo",  # Missing owner
            "owner/repo/extra",  # Too many parts
            "owner with spaces/repo",  # Invalid characters
            "../etc/passwd",  # Path traversal
        ],
    )
    def test_validate_invalid_repository_identifiers(self, invalid_identifier):
        with pytest.raises((InvalidRepositoryError, SuspiciousOperationError)):
            InputValidator.validate_repository_identifier(invalid_identifier)

    def test_validate_repository_identifier_too_long(self):
        long_identifier = "a" * 300 + "/repo"
        with pytest.raises(InvalidRepositoryError):
            InputValidator.validate_repository_identifier(long_identifier)

    def test_validate_repository_identifier_convenience_function(self):
        owner, repo = validate_repository_identifier("test-org/test-repo")
        assert owner == "test-org"
        assert repo == "test-repo"


@pytest.mark.unit
class TestGitHubOwnerValidation:
    @pytest.mark.parametrize(
        "valid_owner",
        [
            "owner",
            "test-user",
            "user_123",
            "a",  # Single character
            "a" * 39,  # Max length (39 chars)
        ],
    )
    def test_validate_valid_github_owners(self, valid_owner):
        InputValidator._validate_github_owner(valid_owner)

    @pytest.mark.parametrize(
        "invalid_owner",
        [
            "",  # Empty
            "a" * 40,  # Too long
            "user@name",  # Invalid character
            "user.name",  # Dot not allowed in owners
            "user name",  # Space not allowed
        ],
    )
    def test_validate_invalid_github_owners(self, invalid_owner):
        with pytest.raises(InvalidRepositoryError):
            InputValidator._validate_github_owner(invalid_owner)


@pytest.mark.unit
class TestRepoNameValidation:
    @pytest.mark.parametrize(
        "valid_repo",
        [
            "repo",
            "test-repo",
            "repo_123",
            "repo.name",
            "a",  # Single character
            "a" * 100,  # Max length (100 chars)
        ],
    )
    def test_validate_valid_repo_names(self, valid_repo):
        InputValidator._validate_repo_name(valid_repo)

    @pytest.mark.parametrize(
        "invalid_repo",
        [
            "",  # Empty
            "a" * 101,  # Too long
            "repo name",  # Space not allowed
            "repo@name",  # @ not allowed
        ],
    )
    def test_validate_invalid_repo_names(self, invalid_repo):
        with pytest.raises(InvalidRepositoryError):
            InputValidator._validate_repo_name(invalid_repo)


@pytest.mark.unit
class TestStringSanitization:
    def test_sanitize_simple_string(self):
        result = InputValidator.sanitize_string("Hello World")
        assert result == "Hello World"

    def test_sanitize_string_with_tabs_and_newlines(self):
        result = InputValidator.sanitize_string("Line1\nLine2\tTabbed")
        assert result == "Line1\nLine2\tTabbed"

    def test_sanitize_string_removes_control_characters(self):
        result = InputValidator.sanitize_string("Text\x01\x02End")
        assert "\x01" not in result
        assert "\x02" not in result
        assert "Text" in result
        assert "End" in result

    def test_sanitize_string_rejects_null_bytes(self):
        with pytest.raises(InputSanitizationError, match="null bytes"):
            InputValidator.sanitize_string("Text\x00Data")

    def test_sanitize_string_max_length(self):
        with pytest.raises(InputSanitizationError, match="String too long"):
            InputValidator.sanitize_string("a" * 2000, max_length=1000)

    def test_sanitize_string_detects_command_injection(self):
        with pytest.raises(SuspiciousOperationError):
            InputValidator.sanitize_string("text; rm -rf /")

    def test_sanitize_string_detects_sql_injection(self):
        with pytest.raises(SuspiciousOperationError):
            InputValidator.sanitize_string("admin' OR 1=1--")

    def test_sanitize_string_non_string_type(self):
        with pytest.raises(InputSanitizationError, match="Expected string"):
            InputValidator.sanitize_string(cast(Any, 12345))

    def test_sanitize_string_convenience_function(self):
        result = sanitize_string("Test String")
        assert result == "Test String"


@pytest.mark.unit
class TestPRNumberValidation:
    @pytest.mark.parametrize("valid_pr", [1, 100, 999999, 1000000])
    def test_validate_valid_pr_numbers(self, valid_pr):
        result = InputValidator.validate_pr_number(valid_pr)
        assert result == valid_pr

    @pytest.mark.parametrize(
        "invalid_pr,error_match",
        [
            (0, "must be positive"),
            (-1, "must be positive"),
            (1000001, "too large"),
            ("123", "must be an integer"),
            (12.5, "must be an integer"),
        ],
    )
    def test_validate_invalid_pr_numbers(self, invalid_pr, error_match):
        with pytest.raises(InvalidPRNumberError, match=error_match):
            InputValidator.validate_pr_number(invalid_pr)


@pytest.mark.unit
class TestTokenValidation:
    def test_validate_valid_token(self):
        token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        result = InputValidator.validate_token(token)
        assert result == token

    def test_validate_token_with_special_chars(self):
        token = "token-with_dots.and-dashes-123"
        result = InputValidator.validate_token(token)
        assert result == token

    def test_validate_token_too_short(self):
        with pytest.raises(InputSanitizationError, match="too short"):
            InputValidator.validate_token("short")

    def test_validate_token_too_long(self):
        with pytest.raises(InputSanitizationError, match="too long"):
            InputValidator.validate_token("a" * 600)

    def test_validate_token_with_whitespace(self):
        with pytest.raises(InputSanitizationError, match="whitespace"):
            InputValidator.validate_token(" token123456789012345 ")

    def test_validate_token_invalid_characters(self):
        with pytest.raises(InputSanitizationError, match="invalid characters"):
            InputValidator.validate_token("token@with#special!chars123")

    def test_validate_token_empty(self):
        with pytest.raises(InputSanitizationError, match="cannot be empty"):
            InputValidator.validate_token("")

    def test_validate_token_non_string(self):
        with pytest.raises(InputSanitizationError, match="must be a string"):
            InputValidator.validate_token(cast(Any, 123456789012345678901))

    def test_validate_token_convenience_function(self):
        token = "valid_token_1234567890abcdef"
        result = validate_token(token)
        assert result == token


@pytest.mark.unit
class TestUserIDValidation:
    @pytest.mark.parametrize(
        "valid_user_id",
        [
            "user123",
            "user@example.com",
            "user.name",
            "user-name",
            "user_name",
            "a",  # Single character
        ],
    )
    def test_validate_valid_user_ids(self, valid_user_id):
        result = InputValidator.validate_user_id(valid_user_id)
        assert result == valid_user_id

    def test_validate_user_id_too_long(self):
        with pytest.raises(InputSanitizationError, match="too long"):
            InputValidator.validate_user_id("a" * 150)

    def test_validate_user_id_empty(self):
        with pytest.raises(InputSanitizationError, match="cannot be empty"):
            InputValidator.validate_user_id("")

    def test_validate_user_id_invalid_characters(self):
        with pytest.raises(InputSanitizationError, match="invalid characters"):
            InputValidator.validate_user_id("user#name!")

    def test_validate_user_id_non_string(self):
        with pytest.raises(InputSanitizationError, match="must be a string"):
            InputValidator.validate_user_id(cast(Any, 12345))

    def test_validate_user_id_convenience_function(self):
        user_id = "test_user@example.com"
        result = validate_user_id(user_id)
        assert result == user_id


@pytest.mark.unit
class TestFilePathValidation:
    @pytest.mark.parametrize(
        "valid_path",
        [
            "cache/file.json",
            "data/pr_123.txt",
            "output.csv",
        ],
    )
    def test_validate_valid_file_paths(self, valid_path):
        result = InputValidator.validate_file_path(valid_path)
        assert result == valid_path

    @pytest.mark.parametrize(
        "invalid_path,error_type",
        [
            ("../../etc/passwd", SuspiciousOperationError),  # Path traversal
            ("~/secrets", SuspiciousOperationError),  # Home directory
            (
                "/etc/passwd",
                SuspiciousOperationError,
            ),  # System file (detected by pattern)
            (
                "/var/log/app.log",
                SuspiciousOperationError,
            ),  # System directory (detected by pattern)
            ("a" * 600, InputSanitizationError),  # Too long
        ],
    )
    def test_validate_invalid_file_paths(self, invalid_path, error_type):
        with pytest.raises(error_type):
            InputValidator.validate_file_path(invalid_path)

    def test_validate_file_path_empty(self):
        with pytest.raises(InputSanitizationError, match="cannot be empty"):
            InputValidator.validate_file_path("")

    def test_validate_file_path_non_string(self):
        with pytest.raises(InputSanitizationError, match="must be a string"):
            InputValidator.validate_file_path(cast(Any, 123))


@pytest.mark.unit
class TestSafeLogging:
    def test_sanitize_for_logging_simple(self):
        result = InputValidator.sanitize_for_logging("Simple log message")
        assert result == "Simple log message"

    def test_sanitize_for_logging_truncates_long_values(self):
        long_value = "a" * 500
        result = InputValidator.sanitize_for_logging(long_value, max_length=200)
        assert len(result) == 203  # 200 + "..."
        assert result.endswith("...")

    def test_sanitize_for_logging_removes_control_characters(self):
        result = InputValidator.sanitize_for_logging("Text\x00\x01\x02End")
        assert "\x00" not in result
        assert "?" in result  # Control chars replaced with ?

    def test_sanitize_for_logging_preserves_printable_whitespace(self):
        result = InputValidator.sanitize_for_logging("Line1\nLine2\tTabbed")
        assert "\n" in result
        assert "\t" in result

    def test_sanitize_for_logging_converts_non_strings(self):
        result = InputValidator.sanitize_for_logging(cast(Any, 12345))
        assert result == "12345"

    def test_sanitize_for_logging_with_custom_length(self):
        result = InputValidator.sanitize_for_logging("a" * 100, max_length=50)
        assert len(result) == 53  # 50 + "..."


@pytest.mark.unit
class TestSuspiciousPatternDetection:
    @pytest.mark.parametrize(
        "malicious_input",
        [
            "text; echo 'hacked'",  # Command injection with semicolon
            "data && cat /etc/passwd",  # Command injection with &&
            "value | grep password",  # Pipe character
            "cmd `whoami`",  # Backticks
            "exec $(id)",  # Command substitution
        ],
    )
    def test_detect_command_injection_patterns(self, malicious_input):
        result = _detector.check_suspicious_patterns(malicious_input)
        assert result is True

    @pytest.mark.parametrize(
        "malicious_input",
        [
            "../../../etc/passwd",  # Path traversal
            "~/evil/script.sh",  # Home directory
            "/etc/shadow",  # System file
            "/var/www/config",  # System directory
            "/usr/bin/malware",  # System directory
        ],
    )
    def test_detect_path_traversal_patterns(self, malicious_input):
        result = _detector.check_suspicious_patterns(malicious_input)
        assert result is True

    @pytest.mark.parametrize(
        "malicious_input",
        [
            "1' UNION SELECT * FROM users--",  # UNION attack
            "'; DROP TABLE users;--",  # SQL comment
            "1' OR 1=1--",  # SQL comment detected
            "admin'--",  # SQL comment detected
            "EXEC xp_cmdshell 'dir'",  # Stored procedure
            "data WHERE id=1; DELETE FROM users",  # DELETE keyword
        ],
    )
    def test_detect_sql_injection_patterns(self, malicious_input):
        result = _detector.check_suspicious_patterns(malicious_input)
        assert result is True

    @pytest.mark.parametrize(
        "safe_input",
        [
            "normal text",
            "user@example.com",
            "value-with-dashes",
            "path/to/file.txt",
            "123456",
        ],
    )
    def test_safe_inputs_pass_pattern_detection(self, safe_input):
        result = _detector.check_suspicious_patterns(safe_input)
        assert result is False
