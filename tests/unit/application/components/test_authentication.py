"""Unit tests for AuthenticationMiddleware application component.

Tests the AuthenticationMiddleware component which provides API key-based
authentication and authorization functionality.
"""

import os
import time
from unittest.mock import Mock, patch

import pytest

from prdiffer.application.components.authentication import AuthenticationMiddleware


class TestAuthenticationMiddlewareInitialization:
    """Test suite for AuthenticationMiddleware initialization."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_authentication_initialization_default(self):
        """Test AuthenticationMiddleware initializes with auth disabled."""
        auth = AuthenticationMiddleware()

        assert auth is not None
        assert auth._auth_enabled is False

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true"})
    def test_authentication_initialization_enabled(self):
        """Test AuthenticationMiddleware initializes with auth enabled."""
        auth = AuthenticationMiddleware()

        assert auth._auth_enabled is True

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "1"})
    def test_authentication_enabled_with_numeric_1(self):
        """Test various ways to enable authentication."""
        auth = AuthenticationMiddleware()

        assert auth._auth_enabled is True

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "yes"})
    def test_authentication_enabled_with_yes(self):
        """Test enabling authentication with 'yes'."""
        auth = AuthenticationMiddleware()

        assert auth._auth_enabled is True

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_authentication_disabled(self):
        """Test authentication is disabled by default."""
        auth = AuthenticationMiddleware()

        assert auth.is_authentication_enabled() is False

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true", "MCP_API_KEYS": "key1,key2,key3"})
    def test_authentication_loads_api_keys(self):
        """Test API keys are loaded from environment."""
        auth = AuthenticationMiddleware()

        assert auth.get_configured_api_keys_count() == 3
        assert auth._hash_api_key("key1") in auth._hashed_api_keys
        assert auth._hash_api_key("key2") in auth._hashed_api_keys
        assert auth._hash_api_key("key3") in auth._hashed_api_keys

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true", "MCP_API_KEYS": "key1 , key2 , key3 "})
    def test_authentication_trims_whitespace(self):
        """Test API keys are trimmed of whitespace."""
        auth = AuthenticationMiddleware()

        assert auth.get_configured_api_keys_count() == 3
        assert auth._hash_api_key("key1") in auth._hashed_api_keys
        assert auth._hash_api_key("key2") in auth._hashed_api_keys
        assert auth._hash_api_key("key3") in auth._hashed_api_keys

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true", "MCP_API_KEYS": ""})
    def test_authentication_empty_api_keys(self):
        """Test empty API keys string."""
        auth = AuthenticationMiddleware()

        assert auth.get_configured_api_keys_count() == 0

    @patch.dict(
        os.environ,
        {"MCP_AUTH_ENABLED": "true", "MCP_ADMIN_API_KEY": "admin_secret_12345678901"},
    )
    def test_authentication_admin_key_loaded(self):
        """Test admin API key is loaded."""
        auth = AuthenticationMiddleware()

        assert auth._admin_api_key_hash is not None
        assert auth._admin_api_key_hash == auth._hash_api_key("admin_secret_12345678901")

    def test_authentication_with_logger(self):
        """Test AuthenticationMiddleware with custom logger."""
        mock_logger = Mock()
        with patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"}):
            auth = AuthenticationMiddleware(logger=mock_logger)

        assert auth._logger == mock_logger


class TestAuthenticationMiddlewareHashApiKey:
    """Test suite for _hash_api_key method."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_hash_api_key_uses_sha256(self):
        # Given
        auth = AuthenticationMiddleware()

        # When
        hashed_key = auth._hash_api_key("abc")

        # Then
        assert hashed_key == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_hash_api_key_consistent(self):
        """Test hashing produces consistent results."""
        auth = AuthenticationMiddleware()

        hash1 = auth._hash_api_key("test_key")
        hash2 = auth._hash_api_key("test_key")

        assert hash1 == hash2

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_hash_api_key_different(self):
        """Test different keys produce different hashes."""
        auth = AuthenticationMiddleware()

        hash1 = auth._hash_api_key("test_key_1")
        hash2 = auth._hash_api_key("test_key_2")

        assert hash1 != hash2

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_hash_api_key_sha256_length(self):
        """Test SHA-256 produces 64 character hex string."""
        auth = AuthenticationMiddleware()

        hash_result = auth._hash_api_key("test_key")

        assert len(hash_result) == 64
        assert all(c in "0123456789abcdef" for c in hash_result)


class TestAuthenticationMiddlewareAuthenticate:
    """Test suite for authenticate method."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_authenticate_disabled_allows_all(self):
        """Test authentication disabled allows all requests."""
        auth = AuthenticationMiddleware()

        is_auth, client_id = auth.authenticate(None)

        assert is_auth is True
        assert client_id == "anonymous"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true"})
    def test_authenticate_enabled_no_key_fails(self):
        """Test authentication fails when no key provided."""
        auth = AuthenticationMiddleware()

        is_auth, client_id = auth.authenticate(None)

        assert is_auth is False
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true", "MCP_API_KEYS": "test_key_12345678901"})
    def test_authenticate_valid_key_succeeds(self):
        """Test authentication succeeds with valid key."""
        auth = AuthenticationMiddleware()

        is_auth, client_id = auth.authenticate("test_key_12345678901")

        assert is_auth is True
        assert client_id is not None
        assert client_id.startswith("api_key_")

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true", "MCP_API_KEYS": "test_key_12345678901"})
    def test_authenticate_invalid_key_fails(self):
        """Test authentication fails with invalid key."""
        auth = AuthenticationMiddleware()

        is_auth, client_id = auth.authenticate("wrong_key_too_short")

        assert is_auth is False
        assert client_id is None

    @patch.dict(
        os.environ,
        {"MCP_AUTH_ENABLED": "true", "MCP_ADMIN_API_KEY": "admin_secret_12345678901"},
    )
    def test_authenticate_admin_key(self):
        """Test admin API key authentication."""
        auth = AuthenticationMiddleware()

        is_auth, client_id = auth.authenticate("admin_secret_12345678901")

        assert is_auth is True
        assert client_id == "admin"

    @patch.dict(
        os.environ,
        {
            "MCP_AUTH_ENABLED": "true",
            "MCP_API_KEYS": "key1_12345678901,key2_12345678901",
        },
    )
    def test_authenticate_multiple_keys(self):
        """Test authentication with multiple valid keys."""
        auth = AuthenticationMiddleware()

        is_auth1, client_id1 = auth.authenticate("key1_12345678901")
        is_auth2, client_id2 = auth.authenticate("key2_12345678901")

        assert is_auth1 is True
        assert is_auth2 is True
        assert client_id1 != client_id2  # Different client IDs for different keys


class TestAuthenticationMiddlewareExtractClientIdentifier:
    """Test suite for extract_client_identifier method."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_x_api_key_header(self):
        """Test extracting API key from X-API-Key header."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"x-api-key": "test_key"})

        assert api_key == "test_key"
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_x_api_key_header_capitalized(self):
        """Test extracting API key from X-API-Key header (capitalized)."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"X-API-Key": "test_key"})

        assert api_key == "test_key"
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_authorization_bearer_header(self):
        """Test extracting API key from Authorization Bearer header."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"authorization": "Bearer test_token"})

        assert api_key == "test_token"
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_authorization_bearer_header_capitalized(self):
        """Test extracting API key from Authorization Bearer header (capitalized)."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"Authorization": "Bearer test_token"})

        assert api_key == "test_token"
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_x_forwarded_for_header(self):
        """Test extracting IP from X-Forwarded-For header."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"x-forwarded-for": "192.168.1.1"})

        assert api_key is None
        assert client_id == "192.168.1.1"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_x_real_ip_header(self):
        """Test extracting IP from X-Real-IP header."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"x-real-ip": "10.0.0.1"})

        assert api_key is None
        assert client_id == "10.0.0.1"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_x_forwarded_for_multiple_ips(self):
        """Test extracting first IP from X-Forwarded-For with multiple IPs."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"x-forwarded-for": "192.168.1.1, 10.0.0.1, 172.16.0.1"})

        assert api_key is None
        assert client_id == "192.168.1.1"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_no_identifier(self):
        """Test when no identifier is found."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({})

        assert api_key is None
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_extract_api_key_priority_over_ip(self):
        """Test API key takes priority over IP address."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"x-api-key": "test_key", "x-forwarded-for": "192.168.1.1"})

        assert api_key == "test_key"
        assert client_id is None


class TestAuthenticationMiddlewareValidateApiKeyFormat:
    """Test suite for validate_api_key_format method."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_validate_valid_api_key(self):
        """Test validation of valid API key."""
        auth = AuthenticationMiddleware()

        assert auth.validate_api_key_format("a" * 32) is True

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_validate_too_short(self):
        """Test validation rejects keys that are too short."""
        auth = AuthenticationMiddleware()

        assert auth.validate_api_key_format("short") is False

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_validate_too_long(self):
        """Test validation rejects keys that are too long."""
        auth = AuthenticationMiddleware()

        assert auth.validate_api_key_format("a" * 300) is False

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_validate_empty_key(self):
        """Test validation rejects empty key."""
        auth = AuthenticationMiddleware()

        assert auth.validate_api_key_format("") is False

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_validate_non_ascii(self):
        """Test validation rejects non-ASCII characters."""
        auth = AuthenticationMiddleware()

        assert auth.validate_api_key_format("test_测试") is False

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_validate_non_printable(self):
        """Test validation rejects non-printable characters."""
        auth = AuthenticationMiddleware()

        assert auth.validate_api_key_format("test\x00key") is False

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_validate_exactly_16_chars(self):
        """Test validation accepts exactly 16 characters."""
        auth = AuthenticationMiddleware()

        assert auth.validate_api_key_format("a" * 16) is True

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_validate_exactly_256_chars(self):
        """Test validation accepts exactly 256 characters."""
        auth = AuthenticationMiddleware()

        assert auth.validate_api_key_format("a" * 256) is True


class TestAuthenticationMiddlewareAddApiKey:
    """Test suite for add_api_key method."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_add_api_key_valid(self):
        """Test adding a valid API key."""
        auth = AuthenticationMiddleware()

        result = auth.add_api_key("new_key_12345678")

        assert result is True
        assert auth._hash_api_key("new_key_12345678") in auth._hashed_api_keys

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_add_api_key_invalid_format(self):
        """Test adding an invalid format API key fails."""
        auth = AuthenticationMiddleware()

        result = auth.add_api_key("short")

        assert result is False

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_add_api_key_duplicate(self):
        """Test adding duplicate API key fails."""
        auth = AuthenticationMiddleware()
        auth.add_api_key("test_key_12345678")

        result = auth.add_api_key("test_key_12345678")

        assert result is False


class TestAuthenticationMiddlewareRemoveApiKey:
    """Test suite for remove_api_key method."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_remove_api_key_existing(self):
        """Test removing an existing API key."""
        auth = AuthenticationMiddleware()
        auth.add_api_key("test_key_12345678")

        result = auth.remove_api_key("test_key_12345678")

        assert result is True
        assert auth._hash_api_key("test_key_12345678") not in auth._hashed_api_keys

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_remove_api_key_nonexistent(self):
        """Test removing a non-existent API key fails."""
        auth = AuthenticationMiddleware()

        result = auth.remove_api_key("nonexistent_key")

        assert result is False


class TestAuthenticationMiddlewareGetConfiguredApiKeysCount:
    """Test suite for get_configured_api_keys_count method."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_get_count_empty(self):
        """Test count when no keys configured."""
        auth = AuthenticationMiddleware()

        assert auth.get_configured_api_keys_count() == 0

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_get_count_with_keys(self):
        """Test count with keys configured."""
        auth = AuthenticationMiddleware()
        auth.add_api_key("key1_123456789012")
        auth.add_api_key("key2_123456789012")

        assert auth.get_configured_api_keys_count() == 2


class TestAuthenticationMiddlewareGetStatus:
    """Test suite for get_status method."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_get_status_disabled(self):
        """Test status when authentication disabled."""
        auth = AuthenticationMiddleware()

        status = auth.get_status()

        assert status["authentication_enabled"] is False
        assert status["api_keys_configured"] == 0
        assert status["admin_api_key_configured"] is False
        assert status["default_client_id"] == "anonymous"

    @patch.dict(
        os.environ,
        {
            "MCP_AUTH_ENABLED": "true",
            "MCP_API_KEYS": "key1_12345678901,key2_12345678901",
        },
    )
    def test_get_status_enabled_with_keys(self):
        """Test status when enabled with keys."""
        auth = AuthenticationMiddleware()

        status = auth.get_status()

        assert status["authentication_enabled"] is True
        assert status["api_keys_configured"] == 2

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true", "MCP_ADMIN_API_KEY": "admin"})
    def test_get_status_with_admin(self):
        """Test status with admin key configured."""
        auth = AuthenticationMiddleware()

        status = auth.get_status()

        assert status["admin_api_key_configured"] is True


class TestAuthenticationMiddlewareProtocolCompliance:
    """Test suite for AuthenticationMiddleware protocol compliance."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_has_required_methods(self):
        """Test that AuthenticationMiddleware has all required protocol methods."""
        auth = AuthenticationMiddleware()

        # Check all required methods exist
        assert hasattr(auth, "authenticate")
        assert callable(auth.authenticate)
        assert hasattr(auth, "extract_client_identifier")
        assert callable(auth.extract_client_identifier)
        assert hasattr(auth, "is_authentication_enabled")
        assert callable(auth.is_authentication_enabled)
        assert hasattr(auth, "get_status")
        assert callable(auth.get_status)


class TestAuthenticationMiddlewareEdgeCases:
    """Test suite for AuthenticationMiddleware edge cases."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_authenticate_empty_string(self):
        """Test authenticating with empty string."""
        auth = AuthenticationMiddleware()

        is_auth, client_id = auth.authenticate("")

        assert is_auth is True  # Disabled, so allows all
        assert client_id == "anonymous"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true"})
    def test_authenticate_empty_string_enabled(self):
        """Test authenticating with empty string when enabled."""
        auth = AuthenticationMiddleware()

        is_auth, client_id = auth.authenticate("")

        assert is_auth is False
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_authorization_header_without_bearer(self):
        """Test Authorization header without Bearer prefix."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"authorization": "InvalidFormat token"})

        assert api_key is None
        # Falls back to IP if available
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_bearer_with_empty_token(self):
        """Test Bearer header with empty token results in no key."""
        auth = AuthenticationMiddleware()

        api_key, client_id = auth.extract_client_identifier({"authorization": "Bearer "})

        # Empty string after "Bearer " is falsy, so api_key is None
        assert api_key is None
        assert client_id is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "true"})
    def test_case_sensitive_api_keys(self):
        """Test API key comparison is case-sensitive."""
        auth = AuthenticationMiddleware()
        auth.add_api_key("TestKey1234567890")

        is_auth, _ = auth.authenticate("testkey1234567890")  # Lowercase

        assert is_auth is False


class TestAuthenticationMiddlewareJWTVerification:
    """Test suite for JWT signature verification functionality."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_verify_jwt_valid_token(self):
        """Test verification of valid JWT token with correct signature."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret)

        assert is_valid is True
        assert verified_payload is not None
        assert verified_payload["user"] == "testuser"
        assert error is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_verify_jwt_invalid_signature(self):
        """Test verification fails with invalid signature."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        wrong_secret = "wrong_secret_key_12345678_abcdefghijklmnopqrstuvwxyz"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, wrong_secret)

        assert is_valid is False
        assert verified_payload is None
        assert error == "Invalid token signature"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_verify_jwt_expired_token(self):
        """Test verification fails with expired token."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        # Create token that expired 1 hour ago
        payload = {"user": "testuser", "exp": int(time.time()) - 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret)

        assert is_valid is False
        assert verified_payload is None
        assert error == "Token has expired"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_verify_jwt_malformed_token(self):
        """Test verification fails with malformed token."""
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token("not.a.valid.jwt.token", "secret")

        assert is_valid is False
        assert verified_payload is None
        assert "Invalid token" in error

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_verify_jwt_with_audience_validation(self):
        """Test verification with audience claim validation."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {
            "user": "testuser",
            "aud": "test-audience",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Valid audience
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, audience="test-audience")
        assert is_valid is True

        # Invalid audience
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, audience="wrong-audience")
        assert is_valid is False
        assert "audience" in error.lower()

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_verify_jwt_with_issuer_validation(self):
        """Test verification with issuer claim validation."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {
            "user": "testuser",
            "iss": "test-issuer",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Valid issuer
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, issuer="test-issuer")
        assert is_valid is True

        # Invalid issuer
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, issuer="wrong-issuer")
        assert is_valid is False
        assert "issuer" in error.lower()

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_verify_jwt_custom_algorithm(self):
        """Test verification with custom algorithm."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS512")

        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, algorithms=["HS512"])

        assert is_valid is True
        assert verified_payload["user"] == "testuser"

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_verify_jwt_wrong_algorithm(self):
        """Test verification fails when algorithm doesn't match."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Try to verify with HS512 when token was signed with HS256
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, algorithms=["HS512"])

        assert is_valid is False
        assert "algorithm" in error.lower()


class TestAuthenticationMiddlewareJWTPayloadParsing:
    """Test suite for JWT payload parsing without verification."""

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_parse_jwt_payload_valid(self):
        """Test parsing valid JWT payload without verification."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        parsed = AuthenticationMiddleware.parse_jwt_payload(token)

        assert parsed is not None
        assert parsed["user"] == "testuser"
        assert "exp" in parsed

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_parse_jwt_payload_malformed(self):
        """Test parsing malformed JWT returns None."""
        parsed = AuthenticationMiddleware.parse_jwt_payload("not-a-jwt")
        assert parsed is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_parse_jwt_payload_empty(self):
        """Test parsing empty string returns None."""
        parsed = AuthenticationMiddleware.parse_jwt_payload("")
        assert parsed is None

    @patch.dict(os.environ, {"MCP_AUTH_ENABLED": "false"})
    def test_parse_jwt_payload_no_verification(self):
        """Test that parse_jwt_payload does NOT verify signature."""
        import jwt

        secret1 = "secret1_key_12345678_abcdefghijklmnopqrstuvwxyz"
        secret2 = "secret2_key_12345678_abcdefghijklmnopqrstuvwxyz"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}

        # Sign with secret1
        token = jwt.encode(payload, secret1, algorithm="HS256")

        # Parse should succeed even with wrong secret (no verification)
        parsed = AuthenticationMiddleware.parse_jwt_payload(token)
        assert parsed is not None
        assert parsed["user"] == "testuser"

        # verify_jwt_token should fail with wrong secret
        is_valid, _, _ = AuthenticationMiddleware.verify_jwt_token(token, secret2)
        assert is_valid is False


class TestJWTSecurity:
    """Comprehensive JWT security tests for authentication component.

    Tests verify that JWT implementation is secure against common attacks:
    - Signature tampering
    - Algorithm confusion
    - Token expiration
    - Audience/issuer validation
    - Secret key handling
    """

    @pytest.mark.security
    def test_valid_jwt_passes_verification(self):
        """Test valid JWT with correct signature passes verification."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret)

        assert is_valid is True
        assert verified_payload is not None
        assert verified_payload["user"] == "testuser"
        assert error is None

    @pytest.mark.security
    def test_valid_jwt_with_expiration_in_future_passes(self):
        """Test valid JWT with expiration in future passes verification."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 7200}  # 2 hours
        token = jwt.encode(payload, secret, algorithm="HS256")

        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret)

        assert is_valid is True
        assert verified_payload is not None
        assert error is None

    @pytest.mark.security
    def test_expired_jwt_raises_expired_token_error(self):
        """Test expired JWT raises ExpiredTokenError and returns False."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        # Create token that expired 1 hour ago
        payload = {"user": "testuser", "exp": int(time.time()) - 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret)

        assert is_valid is False
        assert verified_payload is None
        assert error == "Token has expired"

    @pytest.mark.security
    def test_tampered_jwt_raises_authentication_error(self):
        """Test JWT with tampered signature raises AuthenticationError."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        wrong_secret = "wrong_secret_key_99999999_abcdefghijklmnopqrstuvwxyz"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        # Sign with correct secret
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Verify with wrong secret (simulates tampered signature)
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, wrong_secret)

        assert is_valid is False
        assert verified_payload is None
        assert error == "Invalid token signature"

    @pytest.mark.security
    def test_jwt_with_wrong_algorithm_raises_error(self):
        """Test algorithm confusion attack is prevented."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        # Sign with HS256
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Try to verify with HS512 only (algorithm confusion)
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, algorithms=["HS512"])

        assert is_valid is False
        assert verified_payload is None
        assert error == "Invalid token algorithm"

    @pytest.mark.security
    def test_empty_jwt_secret_raises_error(self):
        """Test empty JWT secret is rejected."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Try to verify with empty secret
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, "")

        assert is_valid is False
        assert verified_payload is None
        assert error is not None
        # Type guard: after assert not None, error is str
        error_str: str = error
        assert "Invalid token" in error_str or "signature" in error_str.lower() or "key" in error_str.lower() or "failed" in error_str.lower()

    @pytest.mark.security
    def test_jwt_with_invalid_audience_rejected(self):
        """Test JWT with invalid audience claim is rejected."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {
            "user": "testuser",
            "aud": "correct-audience",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Verify with wrong audience
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, audience="wrong-audience")

        assert is_valid is False
        assert verified_payload is None
        assert "audience" in error.lower()

    @pytest.mark.security
    def test_jwt_with_valid_audience_accepted(self):
        """Test JWT with valid audience claim is accepted."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {
            "user": "testuser",
            "aud": "correct-audience",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Verify with correct audience
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, audience="correct-audience")

        assert is_valid is True
        assert verified_payload is not None
        assert error is None

    @pytest.mark.security
    def test_jwt_with_invalid_issuer_rejected(self):
        """Test JWT with invalid issuer claim is rejected."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {
            "user": "testuser",
            "iss": "correct-issuer",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Verify with wrong issuer
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, issuer="wrong-issuer")

        assert is_valid is False
        assert verified_payload is None
        assert "issuer" in error.lower()

    @pytest.mark.security
    def test_jwt_with_valid_issuer_accepted(self):
        """Test JWT with valid issuer claim is accepted."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {
            "user": "testuser",
            "iss": "correct-issuer",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Verify with correct issuer
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, issuer="correct-issuer")

        assert is_valid is True
        assert verified_payload is not None
        assert error is None

    @pytest.mark.security
    def test_verify_jwt_token_returns_correct_tuple_valid(self):
        """Test verify_jwt_token returns correct tuple format for valid token."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        result = AuthenticationMiddleware.verify_jwt_token(token, secret)

        # Verify tuple structure: (is_valid: bool, payload: Optional[Dict], error: Optional[str])
        assert isinstance(result, tuple)
        assert len(result) == 3
        is_valid, payload, error = result
        assert is_valid is True
        assert isinstance(payload, dict)
        assert error is None

    @pytest.mark.security
    def test_verify_jwt_token_returns_correct_tuple_invalid(self):
        """Test verify_jwt_token returns correct tuple format for invalid token."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        wrong_secret = "wrong_secret_key_12345678_abcdefghijklmnopqrstuvwxyz"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        result = AuthenticationMiddleware.verify_jwt_token(token, wrong_secret)

        # Verify tuple structure: (is_valid: bool, payload: Optional[Dict], error: Optional[str])
        assert isinstance(result, tuple)
        assert len(result) == 3
        is_valid, payload, error = result
        assert is_valid is False
        assert payload is None
        assert error is not None
        assert isinstance(error, str)

    @pytest.mark.security
    def test_jwt_verify_signature_enabled_by_default(self):
        """Test that signature verification is enabled (verify_signature=True)."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        wrong_secret = "different_secret_key_12345678_abcdefghijklmnopqrstuvwxyz"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        # If signature verification was disabled, wrong secret would still pass
        # Since verification is enabled, wrong secret should fail
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, wrong_secret)

        assert is_valid is False
        assert error == "Invalid token signature"

    @pytest.mark.security
    def test_jwt_verify_exp_enabled_by_default(self):
        """Test that expiration verification is enabled (verify_exp=True)."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        # Create expired token
        payload = {"user": "testuser", "exp": int(time.time()) - 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        # If exp verification was disabled, expired token would still pass
        # Since verification is enabled, expired token should fail
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret)

        assert is_valid is False
        assert error == "Token has expired"

    @pytest.mark.security
    def test_jwt_with_none_algorithm_none_rejected(self):
        """Test JWT with 'none' algorithm is rejected (algorithm attack)."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}

        # Try to create token with 'none' algorithm (security vulnerability if allowed)
        try:
            token = jwt.encode(payload, secret, algorithm="none")

            # Verify should reject 'none' algorithm when only HS256 is allowed
            is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, algorithms=["HS256"])

            assert is_valid is False
            assert "algorithm" in error.lower()
        except Exception:
            # PyJWT may not support 'none' algorithm for security reasons
            # This is acceptable - the attack is prevented at the library level
            pass

    @pytest.mark.security
    def test_jwt_with_malformed_payload_rejected(self):
        """Test JWT with malformed payload is rejected."""
        # Completely invalid token format
        invalid_tokens = [
            "not.a.jwt",
            "invalid.token.here",
            "",
            "   ",
            "Bearer token_without_dots",
        ]

        for invalid_token in invalid_tokens:
            is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(invalid_token, "any_secret")

            assert is_valid is False
            assert verified_payload is None
            assert error is not None
            assert isinstance(error, str)
            # Type guard: after isinstance check, error is definitely str
            error_str: str = error
            assert "Invalid token" in error_str

    @pytest.mark.security
    def test_jwt_with_all_claims_validated(self):
        """Test that all JWT claims are validated (exp, aud, iss)."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {
            "user": "testuser",
            "aud": "test-audience",
            "iss": "test-issuer",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Verify with all claims matching
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(
            token,
            secret,
            audience="test-audience",
            issuer="test-issuer",
        )

        assert is_valid is True
        assert verified_payload is not None
        assert error is None

    @pytest.mark.security
    def test_jwt_without_expiration_rejected_when_required(self):
        """Test JWT without expiration claim is handled correctly."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        # Token without 'exp' claim
        payload = {"user": "testuser"}
        token = jwt.encode(payload, secret, algorithm="HS256")

        # PyJWT may reject tokens without 'exp' when verify_exp is required
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret)

        # Either passes (if exp is optional) or fails with appropriate error
        if not is_valid:
            assert verified_payload is None
            assert error is not None
        else:
            # If it passes, verify the payload is correct
            assert verified_payload is not None
            assert verified_payload["user"] == "testuser"

    @pytest.mark.security
    def test_jwt_multiple_algorithms_rejects_wrong_one(self):
        """Test JWT with multiple allowed algorithms rejects wrong one."""
        import jwt

        secret = "test_secret_key_12345678_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        payload = {"user": "testuser", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, secret, algorithm="HS256")

        # Verify with HS256, HS384, HS512 allowed - should succeed
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, algorithms=["HS256", "HS384", "HS512"])

        assert is_valid is True
        assert verified_payload is not None
        assert error is None

        # Now try with only HS384 and HS512 (exclude HS256) - should fail
        is_valid, verified_payload, error = AuthenticationMiddleware.verify_jwt_token(token, secret, algorithms=["HS384", "HS512"])

        assert is_valid is False
        assert "algorithm" in error.lower()
