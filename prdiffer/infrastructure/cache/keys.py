"""Cache key generation and hashing utilities."""

import hashlib
from prdiffer.domain.exceptions import ValidationError
from prdiffer.domain.error_codes import E1010_INVALID_CONFIGURATION


class CacheKeyManager:
    """Manages cache key generation and hashing."""

    def __init__(
        self,
        use_hashed_keys: bool = True,
        hash_algorithm: str = "md5",
        store_key_mapping: bool = True,
    ):
        """Initialize the cache key manager.

        Args:
            use_hashed_keys: Whether to hash cache keys
            hash_algorithm: Hash algorithm to use (md5, sha256, sha256_short)
            store_key_mapping: Whether to store reverse mapping
        """
        self._use_hashed_keys = use_hashed_keys
        self._hash_algorithm = hash_algorithm
        self._store_key_mapping = store_key_mapping

    @property
    def use_hashed_keys(self) -> bool:
        """Whether keys are hashed."""
        return self._use_hashed_keys

    @property
    def hash_algorithm(self) -> str:
        """Hash algorithm being used."""
        return self._hash_algorithm

    def generate_key(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """Generate a cache key from repository and PR info.

        Args:
            repo_owner: Repository owner/organization
            repo_name: Repository name
            pr_number: Pull request number

        Returns:
            Cache key in format "owner/repo/pr/number"
        """
        return f"{repo_owner}/{repo_name}/pr/{pr_number}"

    def hash_key(self, key: str) -> str:
        """Hash a cache key using the configured algorithm.

        Args:
            key: Original cache key

        Returns:
            Hashed key

        Raises:
            ValidationError: If hash algorithm is unsupported
        """
        if self._hash_algorithm == "md5":
            return hashlib.md5(key.encode("utf-8")).hexdigest()
        elif self._hash_algorithm == "sha256":
            return hashlib.sha256(key.encode("utf-8")).hexdigest()
        elif self._hash_algorithm == "sha256_short":
            return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        else:
            raise ValidationError(
                f"Unsupported hash algorithm: {self._hash_algorithm}",
                error_code=E1010_INVALID_CONFIGURATION,
            )

    def get_internal_key(self, original_key: str) -> tuple[str, str]:
        """Get internal key for storage.

        Args:
            original_key: Original cache key

        Returns:
            Tuple of (internal_key, hash_display)
        """
        if not self._use_hashed_keys:
            return original_key, ""

        hashed = self.hash_key(original_key)
        return hashed, f"{hashed[:8]}..."
