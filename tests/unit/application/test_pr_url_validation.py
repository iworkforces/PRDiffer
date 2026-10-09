"""Unit tests for PR URL validation in application layer."""

import pytest

from prdiffer.application.utils.pr_url_parser import parse_pr_url
from prdiffer.domain.exceptions import InvalidURLError
from prdiffer.infrastructure.security.input_validator import InputValidator


@pytest.mark.unit
class TestPRUrlValidation:
    """Validate PR URL parsing through application server."""

    def test_parse_pr_url_supports_pull_and_pulls(self) -> None:
        owner, repo, pr_number = parse_pr_url("https://github.com/owner/repo/pull/123", InputValidator())
        assert owner == "owner"
        assert repo == "repo"
        assert pr_number == 123

        owner, repo, pr_number = parse_pr_url("https://github.com/owner/repo/pulls/456", InputValidator())
        assert owner == "owner"
        assert repo == "repo"
        assert pr_number == 456

    @pytest.mark.parametrize(
        "invalid_url",
        [
            "https://gitlab.com/owner/repo/pull/123",
            "https://github.com/owner/pull/123",
        ],
    )
    def test_parse_pr_url_invalid_format(self, invalid_url: str) -> None:
        with pytest.raises(InvalidURLError):
            parse_pr_url(invalid_url, InputValidator())

    def test_parse_pr_url_non_github(self) -> None:
        with pytest.raises(InvalidURLError):
            parse_pr_url("https://example.com/owner/repo/pull/123", InputValidator())
