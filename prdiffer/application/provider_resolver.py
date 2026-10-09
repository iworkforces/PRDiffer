"""Provider capability resolution for MCP pull-request tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from prdiffer.application.utils.pr_url_parser import PRTarget, normalize_request_url, parse_pr_target
from prdiffer.domain.exceptions import InvalidURLError, ProviderCapabilityUnavailableError
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol
from prdiffer.domain.interfaces.pr_diff_reader import SessionPRDiffReader
from prdiffer.domain.interfaces.protocols import GitLabPROperationsProtocol
from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface


@dataclass(frozen=True, slots=True)
class ProviderTarget:
    """A sanitized and provider-validated pull or merge request target."""

    provider: str
    repo_owner: str
    repo_name: str
    pr_number: int
    url: str
    base_url: str | None = None


class ProviderTargetParser(Protocol):
    """Parse one provider's sanitized request URL."""

    def __call__(self, url: str, input_validator: InputValidatorProtocol, /) -> ProviderTarget | None:
        """Return a target when this parser owns the URL, otherwise ``None``."""
        ...


class ApprovalCapability(Protocol):
    """Approve a validated provider target."""

    async def approve(self, target: ProviderTarget, compliment: str, /, *, expected_head_sha: str | None = None) -> str:
        """Approve the target with the supplied compliment."""
        ...


class DescriptionCapability(Protocol):
    """Update a validated provider target description."""

    async def describe(self, target: ProviderTarget, description: str, /) -> str:
        """Set the target description."""
        ...


@dataclass(frozen=True, slots=True)
class StrictDiffCapability:
    """Strict session reader and coalescing identity namespace for one provider."""

    reader: SessionPRDiffReader
    cache_namespace: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.reader, SessionPRDiffReader):
            raise TypeError("Strict diff capabilities require a session-capable reader")


class ProviderCapabilityResolver:
    """Select provider targets and operation capabilities from independent registrations."""

    def __init__(self) -> None:
        self._parsers: dict[str, ProviderTargetParser] = {}
        self._strict_diff_capabilities: dict[str, StrictDiffCapability] = {}
        self._approval_capabilities: dict[str, ApprovalCapability] = {}
        self._description_capabilities: dict[str, DescriptionCapability] = {}

    def register_parser(self, provider: str, parser: ProviderTargetParser, /) -> None:
        """Register a parser in deterministic selection order."""
        if provider in self._parsers:
            raise ValueError(f"Parser already registered for provider: {provider}")
        self._parsers[provider] = parser

    def register_strict_diff(self, provider: str, capability: StrictDiffCapability, /) -> None:
        """Register strict session diff support for a provider."""
        if provider in self._strict_diff_capabilities:
            raise ValueError(f"Strict diff capability already registered for provider: {provider}")
        self._strict_diff_capabilities[provider] = capability

    def register_approval(self, provider: str, capability: ApprovalCapability, /) -> None:
        """Register approval support for a provider."""
        if provider in self._approval_capabilities:
            raise ValueError(f"Approval capability already registered for provider: {provider}")
        self._approval_capabilities[provider] = capability

    def register_description(self, provider: str, capability: DescriptionCapability, /) -> None:
        """Register description support for a provider."""
        if provider in self._description_capabilities:
            raise ValueError(f"Description capability already registered for provider: {provider}")
        self._description_capabilities[provider] = capability

    def resolve_target(self, url: str, input_validator: InputValidatorProtocol, /) -> ProviderTarget:
        """Resolve a sanitized URL through registered provider parsers."""
        url = normalize_request_url(url)
        matches: list[ProviderTarget] = []
        for parser in self._parsers.values():
            target = parser(url, input_validator)
            if target is not None:
                matches.append(target)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise InvalidURLError("Ambiguous PR URL provider")
        raise InvalidURLError("Unsupported PR URL provider")

    def resolve_strict_diff(self, target: ProviderTarget, /) -> StrictDiffCapability:
        """Resolve strict diff support or raise the provider-neutral capability error."""
        capability = self._strict_diff_capabilities.get(target.provider)
        if capability is None:
            raise ProviderCapabilityUnavailableError("get_pr_diff")
        return capability

    def resolve_approval(self, target: ProviderTarget, /) -> ApprovalCapability:
        """Resolve approval support or raise the provider-neutral capability error."""
        capability = self._approval_capabilities.get(target.provider)
        if capability is None:
            raise ProviderCapabilityUnavailableError("approve_pr")
        return capability

    def resolve_description(self, target: ProviderTarget, /) -> DescriptionCapability:
        """Resolve description support or raise the provider-neutral capability error."""
        capability = self._description_capabilities.get(target.provider)
        if capability is None:
            raise ProviderCapabilityUnavailableError("describe_pr")
        return capability


class GitHubWriteCapability:
    """Compose GitHub write operations behind the provider-neutral capabilities."""

    def __init__(self, repository_factory: Callable[[str, str, int], PRDiffRepositoryInterface]) -> None:
        self._repository_factory = repository_factory

    async def approve(self, target: ProviderTarget, compliment: str, /, *, expected_head_sha: str | None = None) -> str:
        repository = self._repository_factory(target.repo_owner, target.repo_name, target.pr_number)
        return await repository.approve_pr_with_comment(pr_url=target.url, compliment=compliment, expected_head_sha=expected_head_sha)

    async def describe(self, target: ProviderTarget, description: str, /) -> str:
        repository = self._repository_factory(target.repo_owner, target.repo_name, target.pr_number)
        return await repository.update_pr_description(pr_url=target.url, description=description)


class GitLabWriteCapability:
    """Compose GitLab note-then-approve and description operations behind capabilities."""

    def __init__(self, operations: GitLabPROperationsProtocol) -> None:
        self._operations = operations

    async def approve(self, target: ProviderTarget, compliment: str, /, *, expected_head_sha: str | None = None) -> str:
        return await self._operations.approve_pr_with_comment(
            target.repo_owner,
            target.repo_name,
            target.pr_number,
            compliment,
            base_url=target.base_url,
            expected_head_sha=expected_head_sha,
        )

    async def describe(self, target: ProviderTarget, description: str, /) -> str:
        return await self._operations.update_pr_description(
            target.repo_owner,
            target.repo_name,
            target.pr_number,
            description,
            base_url=target.base_url,
        )


def _provider_target_from_parsed(url: str, parsed: PRTarget, /) -> ProviderTarget:
    return ProviderTarget(
        parsed.provider,
        parsed.repo_owner,
        parsed.repo_name,
        parsed.pr_number,
        url,
        parsed.base_url,
    )


def parse_github_target(url: str, input_validator: InputValidatorProtocol, /) -> ProviderTarget | None:
    """Parse GitHub URLs using the established validation path."""
    url = normalize_request_url(url)
    if not url.startswith("https://github.com/"):
        return None
    return _provider_target_from_parsed(url, parse_pr_target(url, input_validator))


def parse_gitlab_target(url: str, input_validator: InputValidatorProtocol, /) -> ProviderTarget | None:
    """Parse GitLab MR URLs using the established validation and URL-part parser."""
    url = normalize_request_url(url)
    if not (url.startswith("https://") and "/-/merge_requests/" in url):
        return None
    return _provider_target_from_parsed(url, parse_pr_target(url, input_validator))


def create_provider_capability_resolver(
    github_reader: SessionPRDiffReader,
    github_repository_factory: Callable[[str, str, int], PRDiffRepositoryInterface],
    gitlab_reader: SessionPRDiffReader | None,
    gitlab_operations: GitLabPROperationsProtocol | None,
) -> ProviderCapabilityResolver:
    """Compose the MCP provider-selection path; VCSProviderRegistry is not used by MCP tools."""
    resolver = ProviderCapabilityResolver()
    resolver.register_parser("github", parse_github_target)
    resolver.register_parser("gitlab", parse_gitlab_target)

    github_write = GitHubWriteCapability(github_repository_factory)
    resolver.register_strict_diff("github", StrictDiffCapability(github_reader, None))
    resolver.register_approval("github", github_write)
    resolver.register_description("github", github_write)

    if gitlab_reader is not None:
        resolver.register_strict_diff("gitlab", StrictDiffCapability(gitlab_reader, "gitlab"))
    if gitlab_operations is not None:
        gitlab_write = GitLabWriteCapability(gitlab_operations)
        resolver.register_approval("gitlab", gitlab_write)
        resolver.register_description("gitlab", gitlab_write)
    return resolver
