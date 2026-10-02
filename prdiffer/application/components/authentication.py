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
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock

from prdiffer.domain.interfaces.protocols import AuthenticationProtocol
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol
from prdiffer.domain.exceptions import AuthenticationError
from prdiffer.domain.error_codes import E2002_AUTH_FAILED
from prdiffer.domain.services.logger import LoggerServiceInterface

from prdiffer.application.components.jwt_handler import JWTHandlerMixin
from prdiffer.application.components.api_key_manager import APIKeyManagerMixin


@dataclass
class AuthFailureRecord:
    """Record of authentication failures for rate limiting."""

    count: int = 0
    first_failure: float = field(default_factory=time.time)
    last_failure: float = field(default_factory=time.time)


class AuthenticationMiddleware(JWTHandlerMixin, APIKeyManagerMixin, AuthenticationProtocol):
    """Component responsible for authentication and authorization.

    Features:
    - API key-based authentication
    - Per-client rate limiting support
    - Configuration-based enable/disable
    - Multiple API key support
    - Client identifier extraction for rate limiting
    - Brute-force protection with exponential backoff
    """

    # Rate limiting configuration
    DEFAULT_MAX_FAILURES_PER_MINUTE = 5
    DEFAULT_LOCKOUT_DURATION = 60  # seconds
    DEFAULT_FAILURE_WINDOW = 300  # 5 minutes

    def __init__(
        self,
        logger: logging.Logger | LoggerServiceInterface | None = None,
        max_failures_per_minute: int = DEFAULT_MAX_FAILURES_PER_MINUTE,
        lockout_duration: int = DEFAULT_LOCKOUT_DURATION,
        failure_window: int = DEFAULT_FAILURE_WINDOW,
        check_token_expiration: bool = True,
        input_validator: InputValidatorProtocol | None = None,
    ):
        """Initialize authentication middleware.

        Args:
            logger: Optional logger instance
            max_failures_per_minute: Maximum failed attempts before lockout
            lockout_duration: Duration of lockout in seconds
            failure_window: Time window for counting failures in seconds
            check_token_expiration: Whether to check JWT token expiration (default: True)
        """
        self._logger = logger or logging.getLogger(__name__)
        if input_validator is None:
            from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

            input_validator = get_infrastructure_factory().create_input_validator()
        self._input_validator = input_validator

        self._auth_enabled = os.getenv("MCP_AUTH_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        self._api_keys_env = os.getenv("MCP_API_KEYS", "")

        self._max_failures_per_minute = max_failures_per_minute
        self._lockout_duration = lockout_duration
        self._failure_window = failure_window

        self._check_token_expiration = check_token_expiration

        self._lock = RLock()
        self._max_auth_failure_records = 500  # DoS prevention: limit number of tracked clients
        self._auth_failures: OrderedDict[str, AuthFailureRecord] = OrderedDict()
        self._locked_clients: dict[str, float] = {}  # client_id -> unlock_time

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

    def _is_locked_out(self, client_identifier: str) -> bool:
        """Check if a client is currently locked out."""
        current_time = time.time()
        with self._lock:
            if client_identifier in self._locked_clients:
                unlock_time = self._locked_clients[client_identifier]
                if current_time < unlock_time:
                    return True
                # Lockout expired, remove it
                del self._locked_clients[client_identifier]
            return False

    def _record_failure(self, client_identifier: str) -> None:
        """Record an authentication failure for a client."""
        current_time = time.time()
        with self._lock:
            # Check if this is a new client and we need to evict oldest (DoS prevention)
            if client_identifier not in self._auth_failures:
                if len(self._auth_failures) >= self._max_auth_failure_records:
                    oldest_client = next(iter(self._auth_failures))
                    del self._auth_failures[oldest_client]
                    self._logger.info(f"Evicted auth failure record for client '{oldest_client}' to make room (max: {self._max_auth_failure_records})")

            if client_identifier in self._auth_failures:
                record = self._auth_failures[client_identifier]
                self._auth_failures.move_to_end(client_identifier)
            else:
                record = AuthFailureRecord()
                self._auth_failures[client_identifier] = record

            time_elapsed = current_time - record.first_failure
            if time_elapsed <= 0:
                time_elapsed = 0.001
            elif time_elapsed > self._failure_window:
                record.count = 1
                record.first_failure = current_time
                time_elapsed = 0.001
            else:
                record.count += 1

            record.last_failure = current_time

    def _record_success(self, client_identifier: str) -> None:
        """Record a successful authentication and clear failures."""
        with self._lock:
            if client_identifier in self._auth_failures:
                del self._auth_failures[client_identifier]

    def _get_client_identifier(self, api_key: str | None) -> str:
        """Get a client identifier for tracking authentication attempts.

        Args:
            api_key: The API key provided (may be None)

        Returns:
            Client identifier string
        """
        if api_key:
            return f"key_{self._hash_api_key(api_key)[:16]}"
        return "anonymous"

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

    def authenticate(self, api_key: str | None) -> tuple[bool, str | None]:
        """Authenticate a request using API key with brute-force protection.

        Args:
            api_key: The API key to validate (may be None for unauthenticated requests)

        Returns:
            Tuple of (is_authenticated, client_id) where:
            - is_authenticated: True if authentication succeeded
            - client_id: Client identifier for rate limiting (None if not authenticated)

        Raises:
            RuntimeError: If client is locked out due to too many failures
        """
        if not self._auth_enabled:
            return True, self._default_client_id

        client_identifier = self._get_client_identifier(api_key)

        if self._is_locked_out(client_identifier):
            self._logger.warning(f"Authentication blocked: Client locked out: {client_identifier[:20]}...")
            raise AuthenticationError(
                "Too many authentication failures. Please try again later.",
                error_code=E2002_AUTH_FAILED,
            )

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
            extra={"failures": self._auth_failures.get(client_identifier, AuthFailureRecord()).count},
        )
        return False, None

    def extract_client_identifier(self, headers: dict[str, str]) -> tuple[str | None, str | None]:
        """Extract client identifier from request headers.

        This method extracts API keys from various header sources:
        - X-API-Key: Standard API key header
        - Authorization: Bearer token format

        For fallback, it uses the X-Forwarded-For or X-Real-IP headers
        to get the client IP address.

        Args:
            headers: Request headers dictionary

        Returns:
            Tuple of (api_key, client_id) where:
            - api_key: The extracted API key (or None if not present)
            - client_id: The client identifier for rate limiting (IP or API key hash)
        """
        api_key = headers.get("x-api-key") or headers.get("X-API-Key")

        if not api_key:
            auth_header = headers.get("authorization") or headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                api_key = auth_header[7:]  # Remove "Bearer " prefix

        client_ip = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or headers.get("x-real-ip") or headers.get("X-Real-IP")

        # If X-Forwarded-For contains multiple IPs, take the first one
        if client_ip and "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()

        if api_key:
            return api_key, None  # authenticate() will hash it
        elif client_ip:
            return None, client_ip
        else:
            return None, None

    def is_authentication_enabled(self) -> bool:
        """Check if authentication is enabled."""
        return self._auth_enabled
