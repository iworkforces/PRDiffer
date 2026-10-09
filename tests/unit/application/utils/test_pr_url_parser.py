from dataclasses import is_dataclass
from collections.abc import Callable
from typing import Any, Protocol

import pytest
from prdiffer.application.utils import pr_url_parser
from prdiffer.application.utils.pr_url_parser import normalize_request_url, parse_pr_url
from prdiffer.domain.exceptions import (
    InvalidURLError,
    InvalidPRNumberError,
    SuspiciousOperationError,
)
from prdiffer.infrastructure.security.input_validator import InputValidator


class PRTarget(Protocol):
    provider: str
    repo_owner: str
    repo_name: str
    pr_number: int


@pytest.mark.unit
class TestNormalizeRequestURL:
    def test_strips_surrounding_whitespace(self) -> None:
        assert normalize_request_url("  https://github.com/owner/repo/pull/1\n") == "https://github.com/owner/repo/pull/1"

    def test_rejects_non_string(self) -> None:
        with pytest.raises(InvalidURLError, match="must be a string"):
            normalize_request_url(None)

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(InvalidURLError, match="empty or whitespace-only"):
            normalize_request_url(" \t\n ")


@pytest.mark.unit
class TestParsePRURL:
    def test_valid_pr_url_with_pull(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/123", InputValidator())
        assert result == ("owner", "repo", 123)

    def test_valid_pr_url_with_pulls(self):
        result = parse_pr_url("https://github.com/owner/repo/pulls/456", InputValidator())
        assert result == ("owner", "repo", 456)

    def test_valid_pr_url_with_hyphen_in_repo(self):
        result = parse_pr_url("https://github.com/owner/my-repo/pull/789", InputValidator())
        assert result == ("owner", "my-repo", 789)

    def test_valid_pr_url_with_underscore_in_repo(self):
        result = parse_pr_url("https://github.com/owner/my_repo/pull/100", InputValidator())
        assert result == ("owner", "my_repo", 100)

    def test_valid_pr_url_with_period_in_repo(self):
        result = parse_pr_url("https://github.com/owner/my.repo/pull/200", InputValidator())
        assert result == ("owner", "my.repo", 200)

    def test_valid_pr_url_with_trailing_slash(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/123/", InputValidator())
        assert result == ("owner", "repo", 123)

    def test_valid_pr_url_with_hyphen_in_owner(self):
        result = parse_pr_url("https://github.com/my-org/repo/pull/123", InputValidator())
        assert result == ("my-org", "repo", 123)

    def test_valid_pr_url_with_large_pr_number(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/999999", InputValidator())
        assert result == ("owner", "repo", 999999)

    def test_pr_url_with_whitespace_stripping(self):
        result = parse_pr_url("  https://github.com/owner/repo/pull/123  ", InputValidator())
        assert result == ("owner", "repo", 123)

    def test_invalid_pr_url_none(self):
        with pytest.raises(InvalidURLError, match="must be a string"):
            bad_input: Any = None
            parse_pr_url(bad_input, InputValidator())

    def test_invalid_pr_url_empty_string(self):
        with pytest.raises(InvalidURLError, match="empty or whitespace-only"):
            parse_pr_url("", InputValidator())

    def test_invalid_pr_url_whitespace_only(self):
        with pytest.raises(InvalidURLError, match="empty or whitespace-only"):
            parse_pr_url("   ", InputValidator())

    def test_invalid_pr_url_wrong_protocol(self):
        with pytest.raises(InvalidURLError, match="https://github.com/"):
            parse_pr_url("http://github.com/owner/repo/pull/123", InputValidator())

    def test_invalid_pr_url_missing_pr_segment(self):
        with pytest.raises(InvalidURLError, match="Invalid GitHub PR URL format"):
            parse_pr_url("https://github.com/owner/repo/123", InputValidator())

    def test_invalid_pr_url_missing_pr_number(self):
        with pytest.raises(InvalidURLError, match="Invalid GitHub PR URL format"):
            parse_pr_url("https://github.com/owner/repo/pull/", InputValidator())

    def test_invalid_pr_url_non_numeric_pr_number(self):
        with pytest.raises(InvalidURLError, match="Invalid GitHub PR URL format"):
            parse_pr_url("https://github.com/owner/repo/pull/abc", InputValidator())

    def test_invalid_pr_url_zero_pr_number(self):
        with pytest.raises(InvalidPRNumberError, match="must be positive"):
            parse_pr_url("https://github.com/owner/repo/pull/0", InputValidator())

    def test_invalid_pr_url_negative_pr_number(self):
        with pytest.raises(InvalidURLError, match="Invalid GitHub PR URL format"):
            parse_pr_url("https://github.com/owner/repo/pull/-1", InputValidator())

    def test_invalid_pr_url_too_large_pr_number(self):
        with pytest.raises(InvalidPRNumberError, match="too large"):
            parse_pr_url("https://github.com/owner/repo/pull/1000001", InputValidator())

    def test_invalid_pr_url_missing_owner(self):
        with pytest.raises(InvalidURLError, match="Invalid GitHub PR URL format"):
            parse_pr_url("https://github.com//repo/pull/123", InputValidator())

    def test_invalid_pr_url_missing_repo(self):
        with pytest.raises(InvalidURLError, match="Invalid GitHub PR URL format"):
            parse_pr_url("https://github.com/owner//pull/123", InputValidator())

    def test_invalid_pr_url_invalid_chars_in_owner(self):
        with pytest.raises((SuspiciousOperationError, InvalidURLError)):
            parse_pr_url("https://github.com/own$er/repo/pull/123", InputValidator())

    def test_invalid_pr_url_invalid_chars_in_repo(self):
        with pytest.raises((SuspiciousOperationError, InvalidURLError)):
            parse_pr_url("https://github.com/owner/re*po/pull/123", InputValidator())

    def test_invalid_pr_url_wrong_domain(self):
        with pytest.raises(InvalidURLError, match="https://github.com/"):
            parse_pr_url("https://gitlab.com/owner/repo/pull/123", InputValidator())

    def test_invalid_pr_url_too_long(self):
        long_url = "https://github.com/owner/repo/pull/123" + "a" * 2000
        with pytest.raises(InvalidURLError, match="too long"):
            parse_pr_url(long_url, InputValidator())

    def test_invalid_pr_url_with_suspicious_pattern(self):
        # InputValidator's pattern detection catches suspicious shell commands
        with pytest.raises((SuspiciousOperationError, InvalidURLError)):
            parse_pr_url("https://github.com/owner/repo/pull/123 && rm -rf", InputValidator())

    def test_pr_url_with_custom_input_validator(self):
        custom_validator = InputValidator()
        result = parse_pr_url("https://github.com/owner/repo/pull/123", custom_validator)
        assert result == ("owner", "repo", 123)

    def test_pr_url_with_explicit_input_validator(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/123", InputValidator())
        assert result == ("owner", "repo", 123)

    def test_pr_url_non_string_input(self):
        with pytest.raises(InvalidURLError, match="must be a string"):
            bad_input: Any = 12345
            parse_pr_url(bad_input, InputValidator())

    def test_pr_url_dict_input(self):
        with pytest.raises(InvalidURLError, match="must be a string"):
            bad_input: Any = {"url": "https://github.com/owner/repo/pull/123"}
            parse_pr_url(bad_input, InputValidator())

    def test_pr_url_list_input(self):
        with pytest.raises(InvalidURLError, match="must be a string"):
            bad_input: Any = ["https://github.com/owner/repo/pull/123"]
            parse_pr_url(bad_input, InputValidator())

    def test_real_world_pr_url(self):
        result = parse_pr_url("https://github.com/facebook/react/pull/12345", InputValidator())
        assert result == ("facebook", "react", 12345)

    def test_pr_url_with_numeric_owner(self):
        result = parse_pr_url("https://github.com/user123/repo/pull/789", InputValidator())
        assert result == ("user123", "repo", 789)

    def test_pr_url_with_numeric_repo(self):
        result = parse_pr_url("https://github.com/owner/repo123/pull/456", InputValidator())
        assert result == ("owner", "repo123", 456)

    def test_pr_url_owner_max_length(self):
        owner = "a" * 39
        result = parse_pr_url(f"https://github.com/{owner}/repo/pull/123", InputValidator())
        assert result == (owner, "repo", 123)

    def test_pr_url_owner_too_long(self):
        owner = "a" * 40
        with pytest.raises(InvalidURLError, match="Owner name too long"):
            parse_pr_url(f"https://github.com/{owner}/repo/pull/123", InputValidator())

    def test_pr_url_repo_max_length(self):
        repo = "a" * 100
        result = parse_pr_url(f"https://github.com/owner/{repo}/pull/123", InputValidator())
        assert result == ("owner", repo, 123)

    def test_pr_url_repo_too_long(self):
        repo = "a" * 101
        with pytest.raises(InvalidURLError, match="Repository name too long"):
            parse_pr_url(f"https://github.com/owner/{repo}/pull/123", InputValidator())


@pytest.mark.unit
class TestParsePRTarget:
    @pytest.mark.parametrize(
        ("url", "provider", "owner", "repository", "number"),
        [
            ("https://github.com/owner/repo/pull/17", "github", "owner", "repo", 17),
            ("https://gitlab.com/owner/repo/-/merge_requests/17", "gitlab", "owner", "repo", 17),
            (
                "https://gitlab.com/group/subgroup/project/-/merge_requests/42",
                "gitlab",
                "group/subgroup",
                "project",
                42,
            ),
        ],
    )
    def test_parse_pr_target_returns_provider_aware_dataclass(
        self,
        url: str,
        provider: str,
        owner: str,
        repository: str,
        number: int,
    ) -> None:
        # Given
        validator = InputValidator()
        parse_pr_target: Callable[[str, InputValidator], PRTarget] = getattr(pr_url_parser, "parse_pr_target")

        # When
        target = parse_pr_target(url, validator)

        # Then
        assert is_dataclass(type(target))
        assert target.provider == provider
        assert target.repo_owner == owner
        assert target.repo_name == repository
        assert target.pr_number == number

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://gitlab.com/group/project/-/merge_requests/1",  # not https
            "https://gitlab.com/group/project/-/tree/main",
            "https://gitlab.com/only-one/-/merge_requests/1",
            "https://gitlab.com/group//project/-/merge_requests/1",
            "https://gitlab.com/group/../project/-/merge_requests/1",
            "https://gitlab.com/group/%2e%2e/project/-/merge_requests/1",
            "https://gitlab.com/group/sub%2Fproject/-/merge_requests/1",
            "https://gitlab.com/group/project/-/merge_requests/0",
            "https://gitlab.com/group/project/-/merge_requests/abc",
            "https://gitlab.com/group/project/-/merge_requests/1?x=1",
            "https://gitlab.com/group/project/-/merge_requests/1#frag",
            "https://gitlab.com/group/project/-/merge_requests/1000001",
            "https://user:pass@gitlab.example.com/g/p/-/merge_requests/1",
        ],
    )
    def test_parse_pr_target_rejects_malformed_gitlab_urls(self, bad_url: str) -> None:
        from prdiffer.domain.exceptions import (
            InvalidPRNumberError,
            InvalidURLError,
            SuspiciousOperationError,
        )

        validator = InputValidator()
        with pytest.raises((InvalidURLError, InvalidPRNumberError, SuspiciousOperationError)):
            pr_url_parser.parse_pr_target(bad_url, validator)

    def test_parse_pr_target_strips_surrounding_whitespace(self) -> None:
        validator = InputValidator()

        github = pr_url_parser.parse_pr_target("  https://github.com/owner/repo/pull/17\n", validator)
        gitlab = pr_url_parser.parse_pr_target("\thttps://gitlab.com/owner/repo/-/merge_requests/17  ", validator)

        assert github.provider == "github"
        assert github.repo_owner == "owner"
        assert github.repo_name == "repo"
        assert github.pr_number == 17
        assert gitlab.provider == "gitlab"
        assert gitlab.repo_owner == "owner"
        assert gitlab.repo_name == "repo"
        assert gitlab.pr_number == 17

    def test_parse_custom_hosted_gitlab_url(self) -> None:
        """Custom-hosted GitLab MR URL (placeholder host)."""
        url = "https://gitlab.example.com/trace-analysis/oh-my-grokbuild/-/merge_requests/1"
        validator = InputValidator()
        target = pr_url_parser.parse_pr_target(url, validator)
        assert target.provider == "gitlab"
        assert target.repo_owner == "trace-analysis"
        assert target.repo_name == "oh-my-grokbuild"
        assert target.pr_number == 1
        assert target.base_url == "https://gitlab.example.com"
