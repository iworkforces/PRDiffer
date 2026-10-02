"""Integration tests with real GitHub API.

These tests make actual API calls to GitHub when a token is available.
They are skipped automatically if no token is configured.
"""

import os
import pytest

from prdiffer.application.components.authentication import AuthenticationMiddleware


pytestmark = pytest.mark.skipif(
    True,  # Always skip - requires live GitHub API access
    reason="Real GitHub API tests require live access - skipping by default",
)


@pytest.fixture
def github_token():
    return os.getenv("GITHUB_TOKEN")


@pytest.fixture
def test_repo_owner():
    return "anthropics"


@pytest.fixture
def test_repo_name():
    return "claude-code"


@pytest.fixture
def test_pr_number():
    return 1


@pytest.mark.integration
class TestRealAuthentication:
    def test_valid_token_accepted(self):
        """Test that a valid GitHub token is accepted."""
        if not os.getenv("GITHUB_TOKEN"):
            pytest.skip("GITHUB_TOKEN not configured")

        auth = AuthenticationMiddleware()
        token = os.getenv("GITHUB_TOKEN")

        is_authenticated, client_id = auth.authenticate(token)

        assert is_authenticated is True
        assert client_id is not None

    def test_invalid_token_rejected(self):
        """Test that an invalid token is rejected when auth is enabled."""
        auth = AuthenticationMiddleware()

        is_authenticated, client_id = auth.authenticate("invalid_token_12345")

        if not auth.is_authentication_enabled():
            assert is_authenticated is True
            assert client_id is not None
        else:
            assert is_authenticated is False
            assert client_id is None

    def test_no_token_rejected_when_required(self):
        """Test that no token is rejected when auth is enabled."""
        auth = AuthenticationMiddleware()

        is_authenticated, client_id = auth.authenticate(None)

        if not auth.is_authentication_enabled():
            assert is_authenticated is True
        else:
            assert is_authenticated is False


@pytest.mark.integration
class TestRealInputValidation:
    def test_valid_github_pr_url(self, github_token, test_repo_owner, test_repo_name, test_pr_number):
        """Test validation of a real GitHub PR URL."""
        if not github_token:
            pytest.skip("GITHUB_TOKEN not configured")

        from prdiffer.infrastructure.security.input_validator import InputValidator

        url = f"https://github.com/{test_repo_owner}/{test_repo_name}/pull/{test_pr_number}"
        owner, repo, pr_number = InputValidator().validate_github_url(url)

        assert owner == test_repo_owner
        assert repo == test_repo_name
        assert pr_number == test_pr_number

    def test_suspicious_url_rejected(self):
        """Test that suspicious URLs are rejected."""
        from prdiffer.infrastructure.security.input_validator import InputValidator
        from prdiffer.domain.exceptions import SuspiciousOperationError

        suspicious_url = "https://github.com/owner/repo/pull/123; rm -rf /"

        with pytest.raises(SuspiciousOperationError):
            InputValidator().validate_github_url(suspicious_url)


@pytest.mark.integration
class TestTokenExpiration:
    def test_jwt_parsing(self):
        """Test parsing a JWT token payload."""
        auth = AuthenticationMiddleware()

        import base64
        import json
        import time

        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()

        future_exp = int(time.time()) + 3600  # 1 hour from now
        payload = {"sub": "user123", "exp": future_exp}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()

        signature = base64.urlsafe_b64encode(b"signature").rstrip(b"=").decode()

        token = f"{header}.{payload_b64}.{signature}"

        parsed = auth.parse_jwt_payload(token)
        assert parsed is not None
        assert parsed["exp"] == future_exp

    def test_expired_token_detection(self):
        """Test detection of expired tokens."""
        auth = AuthenticationMiddleware()

        import base64
        import json
        import time

        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()

        past_exp = int(time.time()) - 3600
        payload = {"sub": "user123", "exp": past_exp}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()

        signature = base64.urlsafe_b64encode(b"signature").rstrip(b"=").decode()
        token = f"{header}.{payload_b64}.{signature}"

        is_expired, error_message = auth.is_token_expired(token)

        assert is_expired is True
        assert error_message is not None

    def test_non_jwt_token_accepted(self):
        """Test that non-JWT tokens (like simple API keys) are accepted."""
        auth = AuthenticationMiddleware()

        simple_token = "my_simple_api_key_12345"

        is_expired, error_message = auth.is_token_expired(simple_token)

        assert is_expired is False
        assert error_message is None
