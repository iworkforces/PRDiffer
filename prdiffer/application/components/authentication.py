"""Authentication component for API key-based access control.

This component provides authentication and authorization functionality
for the MCP server, supporting API key-based access control with
per-client rate limiting integration and brute-force protection.

JWT handling is in jwt_handler.py.
API key management is in api_key_manager.py.

Security Note:
- JWT signature verification is supported via verify_jwt_token() method
- The parse_jwt_payload() method does NOT verify signatures and should
  only be used for extracting metadata (like expiration time)
- For authentication decisions, always use verify_jwt_token() or API keys
"""

import hashlib
import os
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock

from prdiffer.domain.interfaces.protocols import AuthenticationProtocol
from prdiffer.domain.exceptions import AuthenticationError
from prdiffer.domain.error_codes import E2002_AUTH_FAILED
from prdiffer.domain.services.logger import LoggerServiceInterface

from prdiffer.application.components.jwt_handler import JWTHandlerMixin
from prdiffer.application.components.api_key_manager import APIKeyManagerMixin


@dataclass
class AuthFailureRecord:
    """Bounded rolling failures and fixed lock deadline for one transport source."""

    failures: list[float] = field(default_factory=list[float])
    locked_until: float | None = None


class AuthenticationMiddleware(JWTHandlerMixin, APIKeyManagerMixin, AuthenticationProtocol):
    """Component responsible for authentication and authorization.

    Features:
    - API key-based authentication
    - Per-client rate limiting support
    - Configuration-based enable/disable
    - Multiple API key support
    - Client identifier extraction for rate limiting
    - Transport-source brute-force protection with fixed lockouts
    """

    # Rate limiting configuration
    DEFAULT_MAX_FAILURES_PER_MINUTE = 5
    DEFAULT_LOCKOUT_DURATION = 60  # seconds
    DEFAULT_FAILURE_WINDOW = 60  # seconds

    def __init__(
        self,
        logger: logging.Logger | LoggerServiceInterface | None = None,
        max_failures_per_minute: int = DEFAULT_MAX_FAILURES_PER_MINUTE,
        lockout_duration: int = DEFAULT_LOCKOUT_DURATION,
        failure_window: int = DEFAULT_FAILURE_WINDOW,
        check_token_expiration: bool = True,
        clock: Callable[[], float] = time.time,
    ):
        """Initialize authentication middleware.

        Args:
            logger: Optional logger instance
            max_failures_per_minute: Maximum failed attempts before lockout
            lockout_duration: Duration of lockout in seconds
            failure_window: Time window for counting failures in seconds
            check_token_expiration: Whether to check JWT token expiration (default: True)
            clock: Injectable time source for failure windows and lock expiry
        """
        self._logger = logger or logging.getLogger(__name__)

        self._auth_enabled = os.getenv("MCP_AUTH_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        self._api_keys_env = os.getenv("MCP_API_KEYS", "")

        self._max_failures_per_minute = max_failures_per_minute
        self._lockout_duration = lockout_duration
        self._failure_window = failure_window
        self._clock = clock

        self._check_token_expiration = check_token_expiration

        self._lock = RLock()
        self._max_auth_failure_records = 500  # DoS prevention: limit number of tracked clients
        self._auth_failures: dict[str, AuthFailureRecord] = {}

        # Parse API keys from environment and store ONLY hashes (no raw keys)
        self._hashed_api_keys: set[str] = set()
        self._api_key_count: int = 0
        if self._api_keys_env:
            raw_keys = [key.strip() for key in self._api_keys_env.split(",") if key.strip()]
            self._api_key_count = len(raw_keys)
            for key in raw_keys:
                self._hashed_api_keys.add(self._hash_api_key(key))

        self._admin_api_key_hash: str | None = None
        admin_key = os.getenv("MCP_ADMIN_API_KEY", "")
        if admin_key:
            self._admin_api_key_hash = self._hash_api_key(admin_key)

        self._default_client_id = "anonymous"

        self._logger.info(
            "Authentication middleware initialized",
            extra={
                "enabled": self._auth_enabled,
                "api_keys_configured": self._api_key_count,
                "admin_configured": self._admin_api_key_hash is not None,
                "max_failures_per_minute": self._max_failures_per_minute,
                "lockout_duration": self._lockout_duration,
            },
        )

    def _hash_api_key(self, api_key: str) -> str:
        """Hash an API key using SHA-256 for secure storage and comparison."""
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def _reclaim_expired_sources(self, now: float) -> None:
        """Reclaim expired records while the authentication lock is held."""
        expired: list[str] = []
        for source, record in self._auth_failures.items():
            if record.locked_until is not None:
                if now >= record.locked_until:
                    expired.append(source)
            else:
                record.failures[:] = [timestamp for timestamp in record.failures if now - timestamp < self._failure_window]
                if not record.failures:
                    expired.append(source)
        for source in expired:
            del self._auth_failures[source]

    def _record_failure(self, client_identifier: str) -> None:
        """Record an authentication failure for a client."""
        current_time = self._clock()
        with self._lock:
            record = self._auth_failures.setdefault(client_identifier, AuthFailureRecord())
            record.failures.append(current_time)
            if len(record.failures) >= self._max_failures_per_minute:
                record.locked_until = current_time + self._lockout_duration

    def _record_success(self, client_identifier: str) -> None:
        """Record a successful authentication and clear failures."""
        with self._lock:
            if client_identifier in self._auth_failures:
                del self._auth_failures[client_identifier]

    def _looks_like_jwt_token(self, token: str) -> bool:
        """Check if a token looks like a JWT token.

        JWT tokens typically have these characteristics:
        - Contains dots (separates base64 encoded parts)
        - Longer than 40 characters
        - May include 'Bearer' prefix in Authorization header

        This is a simple heuristic check and NOT a security validation.
        Use it to distinguish between API keys and JWT tokens for routing.

        Args:
            token: The token to check

        Returns:
            True if token appears to be a JWT token
        """
        if "." in token:
            return True

        if len(token) > 40:
            return True

        if token.startswith("Bearer "):
            clean_token = token.replace("Bearer ", "")
            if clean_token.startswith("Bearer "):
                return True

        return False

    def authenticate(self, api_key: str | None, *, source: str) -> tuple[bool, str | None]:
        """Authenticate a request using API key with brute-force protection.

        Args:
            api_key: The API key to validate (may be None for unauthenticated requests)
            source: Trusted transport peer identity, independent of credentials

        Returns:
            Tuple of (is_authenticated, client_id) where:
            - is_authenticated: True if authentication succeeded
            - client_id: Client identifier for rate limiting (None if not authenticated)

        Raises:
            AuthenticationError: If source is locked out or state capacity is exhausted
        """
        if not self._auth_enabled:
            return True, self._default_client_id

        with self._lock:
            self._reclaim_expired_sources(self._clock())
            record = self._auth_failures.get(source)
            if record is not None and record.locked_until is not None:
                raise AuthenticationError("Too many authentication failures. Please try again later.", error_code=E2002_AUTH_FAILED)
            if record is None and len(self._auth_failures) >= self._max_auth_failure_records:
                raise AuthenticationError("Authentication source capacity exhausted. Please try again later.", error_code=E2002_AUTH_FAILED)
            return self._authenticate_key(api_key, source)

    def _authenticate_key(self, api_key: str | None, client_identifier: str) -> tuple[bool, str | None]:
        """Validate credentials and update source state under the authentication lock."""

        if not api_key:
            self._record_failure(client_identifier)
            self._logger.warning(
                "Authentication failed: No API key provided",
            )
            return False, None

        if self._check_token_expiration:
            if self._looks_like_jwt_token(api_key):
                _, _ = self.is_token_expired(api_key)
            else:
                if not self.validate_api_key_format(api_key):
                    self._record_failure(client_identifier)
                    self._logger.warning("Authentication failed: Invalid API key format")
                    return False, None
                provided_hash = self._hash_api_key(api_key)
                if self._admin_api_key_hash and provided_hash == self._admin_api_key_hash:
                    self._record_success(client_identifier)
                    self._logger.debug("Admin authentication successful")
                    return True, "admin"
                if provided_hash in self._hashed_api_keys:
                    client_id = f"api_key_{provided_hash[:16]}"
                    self._record_success(client_identifier)
                    self._logger.debug(
                        "API key authentication successful",
                        extra={"client_id": client_id},
                    )
                    return True, client_id
                else:
                    self._record_failure(client_identifier)
                    self._logger.warning("Authentication failed: Invalid API key")
                    return False, None

        provided_hash = self._hash_api_key(api_key)

        if self._admin_api_key_hash and provided_hash == self._admin_api_key_hash:
            self._record_success(client_identifier)
            self._logger.debug(
                "Admin authentication successful",
            )
            return True, "admin"

        if provided_hash in self._hashed_api_keys:
            client_id = f"api_key_{provided_hash[:16]}"
            self._record_success(client_identifier)
            self._logger.debug(
                "API key authentication successful",
                extra={"client_id": client_id},
            )
            return True, client_id

        self._record_failure(client_identifier)
        self._logger.warning(
            "Authentication failed: Invalid API key",
            extra={"failures": len(self._auth_failures[client_identifier].failures)},
        )
        return False, None

    def extract_client_identifier(self, headers: dict[str, str]) -> tuple[str | None, str | None]:
        """Extract client identifier from request headers.

        This method extracts API keys from various header sources:
        - X-API-Key: Standard API key header
        - Authorization: Bearer token format

        Forwarded IP headers are never used as transport identity.

        Args:
            headers: Request headers dictionary

        Returns:
            Tuple of (api_key, client_id) where:
            - api_key: The extracted API key (or None if not present)
            - client_id: None; identity comes from transport context or authentication
        """
        api_key = headers.get("x-api-key") or headers.get("X-API-Key")

        if not api_key:
            auth_header = headers.get("authorization") or headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                api_key = auth_header[7:]  # Remove "Bearer " prefix

        return api_key or None, None

    def is_authentication_enabled(self) -> bool:
        """Check if authentication is enabled."""
        return self._auth_enabled
