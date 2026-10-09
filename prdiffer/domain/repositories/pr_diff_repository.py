"""Repository interface for pull request write operations."""

from abc import ABC, abstractmethod


class PRDiffRepositoryInterface(ABC):
    """Abstract interface for per-PR repository write operations (approve, describe)."""

    @property
    @abstractmethod
    def repo_owner(self) -> str:
        """Repository owner/organization name.

        Returns:
            str: The repository owner/organization name
        """
        pass

    @property
    @abstractmethod
    def repo_name(self) -> str:
        """Repository name.

        Returns:
            str: The repository name
        """
        pass

    @property
    @abstractmethod
    def pr_number(self) -> int:
        """Pull request number.

        Returns:
            int: The pull request number
        """
        pass

    @abstractmethod
    async def approve_pr_with_comment(self, pr_url: str, compliment: str, *, expected_head_sha: str | None = None) -> str:
        """Approve a PR with a compliment comment via the implementing provider.

        Typical GitHub path:
        1. Parses PR URL to extract owner, repo, and PR number
        2. Validates PR exists and is accessible
        3. Creates an APPROVE review with the compliment as body
        4. Returns a success message or raises on failure

        Args:
            pr_url: Full PR URL for the implementing provider
            compliment: Non-empty compliment text included with the approval
            expected_head_sha: Optional normalized head SHA the reviewer read. When
                given, a moved head raises ``HeadSHAMismatchError`` before any review
                is created and the review is bound to that commit.

        Returns:
            str: Success message indicating PR was approved
        """
        pass

    @abstractmethod
    async def update_pr_description(self, pr_url: str, description: str) -> str:
        """Update a PR description/body via the implementing provider.

        Typical GitHub path:
        1. Parses PR URL to extract owner, repo, and PR number
        2. Validates PR exists and is accessible
        3. Updates the PR body/description field
        4. Returns a success message or raises on failure

        Args:
            pr_url: Full PR URL for the implementing provider
            description: Non-empty description text to set on the PR

        Returns:
            str: Success message indicating PR description was updated
        """
        pass
