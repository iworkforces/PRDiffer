import pytest

from prdiffer.domain.entities.gitlab_merge_request_url import (
    GitLabURLParts,
    parse_gitlab_merge_request_parts,
    parse_gitlab_merge_request_url,
)
from prdiffer.domain.exceptions import InvalidPRNumberError, InvalidURLError


@pytest.mark.unit
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://gitlab.com/group/project/-/merge_requests/1",
            GitLabURLParts("https://gitlab.com", "gitlab.com", "group", "project", 1),
        ),
        (
            "https://gitlab.com/group/sub/team/project/-/merge_requests/42",
            GitLabURLParts("https://gitlab.com", "gitlab.com", "group/sub/team", "project", 42),
        ),
        (
            "https://GitLab.Example.COM:8443/group/project/-/merge_requests/17",
            GitLabURLParts("https://gitlab.example.com:8443", "gitlab.example.com", "group", "project", 17),
        ),
        (
            "https://gitlab.com/group/project/-/merge_requests/1/",
            GitLabURLParts("https://gitlab.com", "gitlab.com", "group", "project", 1),
        ),
        (
            "  https://localhost/group/project/-/merge_requests/1000000  ",
            GitLabURLParts("https://localhost", "localhost", "group", "project", 1000000),
        ),
    ],
)
def test_parses_merge_request_parts(url: str, expected: GitLabURLParts) -> None:
    # Given: a supported GitLab URL and its independently specified components.
    # When: the pure parser resolves the target.
    parts = parse_gitlab_merge_request_parts(url)
    # Then: host, namespace, project and request identity are preserved.
    assert parts == expected
    assert parts.project_path == f"{expected.namespace}/{expected.project}"


@pytest.mark.unit
def test_tuple_parser_preserves_nested_namespace() -> None:
    # Given: a nested namespace on a custom host.
    url = "https://gitlab.example.com/group/sub/project/-/merge_requests/42"
    # When: callers request the repository identity tuple.
    identity = parse_gitlab_merge_request_url(url)
    # Then: namespace nesting is retained.
    assert identity == ("group/sub", "project", 42)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://gitlab.com/group/project/-/merge_requests/1",
        "https://user:password@gitlab.com/group/project/-/merge_requests/1",
        "https://gitlab.com/group/project/-/merge_requests/1?view=diff",
        "https://gitlab.com/group/project/-/merge_requests/1#diff",
        "https://gitlab.com/group/../project/-/merge_requests/1",
        "https://gitlab.com/group/./project/-/merge_requests/1",
        "https://gitlab.com/group%2Fsub/project/-/merge_requests/1",
        "https://gitlab.com/group%5Csub/project/-/merge_requests/1",
        "https://gitlab.com/project/-/merge_requests/1",
        "https://bad_host.example/group/project/-/merge_requests/1",
        "https://gitlab.com/group//project/-/merge_requests/1",
        "https://gitlab.com/group/project/merge_requests/1",
        "https://gitlab.com/group/project/-/merge_requests/1/extra",
        "https://gitlab.com/group/project/-/merge_requests/1?",
    ],
)
def test_rejects_malformed_merge_request_url(url: str) -> None:
    # Given: an invalid scheme, authority or path.
    # When / Then: parsing rejects the URL with a domain validation error.
    with pytest.raises(InvalidURLError):
        parse_gitlab_merge_request_parts(url)


@pytest.mark.unit
@pytest.mark.parametrize("iid", ["abc", "0", "1000001"])
def test_rejects_invalid_merge_request_number(iid: str) -> None:
    # Given: an otherwise valid URL with an invalid request number.
    url = f"https://gitlab.com/group/project/-/merge_requests/{iid}"
    # When / Then: the number-specific domain error is preserved.
    with pytest.raises(InvalidPRNumberError):
        parse_gitlab_merge_request_parts(url)
