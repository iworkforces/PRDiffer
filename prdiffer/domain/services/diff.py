"""Diff utility service interface for domain layer."""

from abc import ABC, abstractmethod


class DiffServiceInterface(ABC):
    """Abstract base class for diff services.

    This interface defines the contract for services that provide
    diff generation and manipulation functionality.
    """

    @abstractmethod
    def build_full_file_patch(self, original_file_str: str, new_file_str: str) -> str:
        """Build a full-file unified diff patch.

        Args:
            original_file_str: Original file content
            new_file_str: New file content

        Returns:
            str: Unified diff patch covering the entire file
        """
        pass

    def build_full_file_patch_chunked(self, original_file_str: str, new_file_str: str) -> str:
        return self.build_full_file_patch(original_file_str, new_file_str)
