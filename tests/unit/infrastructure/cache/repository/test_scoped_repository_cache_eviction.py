from unittest.mock import MagicMock, patch

from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface
from prdiffer.infrastructure.cache.cache_repository import RepositoryCacheService


def seeded_repository(cache: RepositoryCacheService, owner: str, repo: str, pr_number: int) -> MagicMock:
    repository = MagicMock(spec=PRDiffRepositoryInterface)
    repository.repo_owner = owner
    repository.repo_name = repo
    repository.pr_number = pr_number
    repository._initialized = True
    cache.insert(repository)
    return repository


def test_github_pr_eviction_removes_only_matching_pr_when_case_differs() -> None:
    cache = RepositoryCacheService()
    seeded_repository(cache, "Acme", "Widgets", 7)
    sibling = seeded_repository(cache, "acme", "widgets", 8)
    other_repo = seeded_repository(cache, "Acme", "Tools", 7)
    other_owner = seeded_repository(cache, "Other", "Widgets", 7)

    cache.invalidate_github_pr("ACME", "WIDGETS", 7)

    assert cache.retrieve("acme", "widgets", 7) is None
    assert cache.retrieve("acme", "widgets", 8) is sibling
    assert cache.retrieve("acme", "tools", 7) is other_repo
    assert cache.retrieve("other", "widgets", 7) is other_owner
    assert cache.size() == 3


def test_github_repository_eviction_removes_all_its_prs_including_zero() -> None:
    cache = RepositoryCacheService()
    for pr_number in (0, 7, 8):
        seeded_repository(cache, "Acme", "Widgets", pr_number)
    other_repo = seeded_repository(cache, "Acme", "Tools", 7)
    other_owner = seeded_repository(cache, "Other", "Widgets", 7)

    cache.invalidate_github_repository("ACME", "widgets")

    assert all(cache.retrieve("acme", "widgets", number) is None for number in (0, 7, 8))
    assert cache.retrieve("acme", "tools", 7) is other_repo
    assert cache.retrieve("other", "widgets", 7) is other_owner
    assert cache.size() == 2


def test_pr_eviction_is_idempotent_and_allows_later_insert() -> None:
    cache = RepositoryCacheService()
    seeded_repository(cache, "Acme", "Widgets", 7)

    cache.invalidate_github_pr("acme", "widgets", 7)
    cache.invalidate_github_pr("acme", "widgets", 7)

    replacement = seeded_repository(cache, "Acme", "Widgets", 7)
    assert cache.retrieve("acme", "widgets", 7) is replacement
    assert cache.size() == 1


def test_repository_eviction_is_idempotent_and_allows_later_insert() -> None:
    cache = RepositoryCacheService()
    seeded_repository(cache, "Acme", "Widgets", 7)

    cache.invalidate_github_repository("acme", "widgets")
    cache.invalidate_github_repository("acme", "widgets")

    replacement = seeded_repository(cache, "Acme", "Widgets", 7)
    assert cache.retrieve("acme", "widgets", 7) is replacement
    assert cache.size() == 1


def test_exact_key_invalidate_still_targets_pr_zero_only() -> None:
    cache = RepositoryCacheService()
    seeded_repository(cache, "Acme", "Widgets", 0)
    sibling = seeded_repository(cache, "Acme", "Widgets", 7)

    assert cache.invalidate("ACME/WIDGETS") is True
    assert cache.retrieve("acme", "widgets", 0) is None
    assert cache.retrieve("acme", "widgets", 7) is sibling
    assert cache.invalidate("acme/widgets/pr/7") is True
    assert cache.size() == 0


def test_scoped_eviction_removes_expired_entries_without_touching_other_repositories() -> None:
    cache = RepositoryCacheService(ttl_seconds=10)
    with patch("prdiffer.infrastructure.cache.cache_repository.time.time", return_value=100):
        seeded_repository(cache, "Acme", "Widgets", 7)
        seeded_repository(cache, "Acme", "Widgets", 8)
        seeded_repository(cache, "Other", "Widgets", 7)

    with patch("prdiffer.infrastructure.cache.cache_repository.time.time", return_value=111):
        cache.invalidate_github_repository("Acme", "Widgets")
        assert cache.size() == 1
        assert cache.stats()["expired_entries"] == 1
        assert cache.retrieve("other", "widgets", 7) is None


def test_scoped_eviction_preserves_size_eviction_behavior() -> None:
    cache = RepositoryCacheService(max_size=2)
    seeded_repository(cache, "Acme", "Widgets", 7)
    seeded_repository(cache, "Acme", "Widgets", 8)
    other_repo = seeded_repository(cache, "Other", "Tools", 1)

    cache.invalidate_github_repository("Acme", "Widgets")
    replacement = seeded_repository(cache, "Acme", "Widgets", 9)

    assert cache.size() == 2
    assert cache.retrieve("other", "tools", 1) is other_repo
    assert cache.retrieve("acme", "widgets", 9) is replacement


def test_clear_still_empties_cache_after_scoped_eviction() -> None:
    cache = RepositoryCacheService()
    seeded_repository(cache, "Acme", "Widgets", 7)
    seeded_repository(cache, "Other", "Tools", 1)
    cache.invalidate_github_repository("Acme", "Widgets")

    cache.clear()

    assert cache.size() == 0
    assert cache.retrieve("other", "tools", 1) is None
