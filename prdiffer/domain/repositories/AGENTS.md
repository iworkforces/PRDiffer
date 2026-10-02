# AGENTS.md - Domain/Repositories

Repository port for per-PR write operations. Package 0.6.2.

## STRUCTURE
```
prdiffer/domain/repositories/
├── pr_diff_repository.py   # PRDiffRepositoryInterface (~75)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **PR write port (GitHub-shaped)** | `pr_diff_repository.py` | Implemented by the GitHub PR repository (URL-bearing approve/describe) |
| **GitLab MR ops** | `GitLabPROperationsProtocol` + `GitLabVCSRepository` | Owner/repo/iid + optional `base_url` (not this ABC) |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `PRDiffRepositoryInterface` | ABC | `pr_diff_repository.py` | Repo-scoped PR write operations (GitHub tool path) |

### Methods / properties
- Properties: `repo_owner`, `repo_name`, `pr_number`
- `approve_pr_with_comment(pr_url, compliment)` → success message (GitHub review)
- `update_pr_description(pr_url, description)` → success message

## CONVENTIONS
- Diffs are read through `SessionPRDiffReader` (`domain/interfaces/pr_diff_reader.py`), not this port.
- GitHub: `infrastructure/github_repository.py` implements this interface for approve/describe MCP GitHub branch.
- GitLab: `infrastructure/vcs_providers/gitlab_repository.py` exposes parallel methods with `(owner, repo, pr, body, *, base_url)` for MCP GitLab branch (`GitLabPROperationsProtocol`).
- Domain use cases for approve/describe still inject this interface; MCP tools may call provider adapters directly after `parse_pr_target`.

## ANTI-PATTERNS
- NO PyGithub / httpx / python-gitlab types in signatures.
- NO I/O defaults or concrete clients in this package.
