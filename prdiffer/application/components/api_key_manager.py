"""API key management for authentication.

Extracted from authentication.py for maintainability.
Contains runtime API key management operations.
"""

from __future__ import annotations

import logging
from typing import Any

from prdiffer.domain.services.logger import LoggerServiceInterface


class APIKeyManagerMixin:
    """Mixin providing API key management capabilities.

    Requires the host class to provide:
        - self._logger: logging.Logger | LoggerServiceInterface
        - self._hashed_api_keys: set[str]
        - self._api_key_count: int
        - self._auth_enabled: bool
        - self._admin_api_key_hash: str | None
        - self._default_client_id: str
    """

    # Type annotations for host class attributes used by this mixin
    _logger: logging.Logger | LoggerServiceInterface
    _hashed_api_keys: set[str]
    _api_key_count: int
    _auth_enabled: bool
    _admin_api_key_hash: str | None
    _default_client_id: str

    def _hash_api_key(self, api_key: str) -> str:
        raise NotImplementedError

    def validate_api_key_format(self, api_key: str) -> bool:
        """Validate API key format before attempting authentication.

        This method provides basic validation to reject obviously invalid
        API keys before attempting authentication.

        Args:
            api_key: The API key to validate

        Returns:
            True if the API key format is valid
        """
        if not api_key:
            return False

        # Check length (API keys should be between 16 and 256 characters)
        if len(api_key) < 16 or len(api_key) > 256:
            return False

        if not api_key.isascii() or not api_key.isprintable():
            return False

        return True

    def add_api_key(self, api_key: str) -> bool:
        """Add a new API key to the valid keys set.

        This method allows runtime addition of API keys.

        Args:
            api_key: The API key to add

        Returns:
            True if the API key was added successfully
        """
        if not self.validate_api_key_format(api_key):
            self._logger.warning("Failed to add API key: Invalid format")
            return False

        api_key_hash = self._hash_api_key(api_key)
        if api_key_hash in self._hashed_api_keys:
            self._logger.warning("API key already exists")
            return False

        self._hashed_api_keys.add(api_key_hash)
        self._api_key_count += 1
        self._logger.info("API key added successfully")
        return True

    def remove_api_key(self, api_key: str) -> bool:
        """Remove an API key from the valid keys set.

        Args:
            api_key: The API key to remove

        Returns:
            True if the API key was removed successfully
        """
        api_key_hash = self._hash_api_key(api_key)
        if api_key_hash in self._hashed_api_keys:
            self._hashed_api_keys.remove(api_key_hash)
            self._api_key_count -= 1
            self._logger.info("API key removed successfully")
            return True
        return False

    def get_configured_api_keys_count(self) -> int:
        """Get the number of configured API keys."""
        return self._api_key_count

    def get_status(self) -> dict[str, Any]:
        """Get authentication status and configuration.

        Returns:
            Dictionary containing authentication status
        """
        return {
            "authentication_enabled": self._auth_enabled,
            "api_keys_configured": self._api_key_count,
            "admin_api_key_configured": self._admin_api_key_hash is not None,
            "default_client_id": self._default_client_id,
        }
