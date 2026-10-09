# AGENTS.md - Application/Utils

Application helpers for MCP tool parameter handling.

## STRUCTURE
```
prdiffer/application/utils/
├── pr_url_parser.py   # Parse/validate PR URLs (~94) — parse_pr_url, parse_pr_target, PRTarget
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **GitHub PR URL** | `parse_pr_url()` | Returns `(owner, repo, number)` via `InputValidatorProtocol` |
| **Provider-aware target** | `parse_pr_target()` | Frozen `PRTarget` for GitHub or GitLab (incl. custom hosts) |
| **PRTarget model** | `PRTarget` dataclass | `provider`, `repo_owner`, `repo_name`, `pr_number`, optional `base_url` |

## CONVENTIONS
- `InputValidatorProtocol` is a required argument; there is no factory fallback (application must not import infrastructure).
- GitLab MR parts come from `prdiffer/domain/entities/gitlab_merge_request_url.py` (`parse_gitlab_merge_request_parts`); host allowlisting stays in infrastructure.
- Raise domain validation errors (`InvalidURLError`, etc.), not raw `ValueError`, at the tool boundary.
- Used by `ToolRegistry` (`get_pr_diff` uses `parse_pr_target` for GitHub/GitLab routing).
- GitLab path: `https://…/-/merge_requests/N` (any allowlisted host); nested namespaces become `repo_owner` with slashes.

## ANTI-PATTERNS
- NO provider SDK calls from utils.
- NO business rules (diff completeness, prioritization) in URL helpers.
- NO treating `parse_pr_url` as multi-provider (GitHub-only helper).
