# AGENTS.md - Infrastructure/Factories

**Package:** 0.6.2  
`InfrastructureFactory` implements domain `InfrastructureFactoryInterface` (~234 lines).

## STRUCTURE
```
prdiffer/infrastructure/factories/
├── infrastructure_factory.py  # create_* for all infra services (~234)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Wire GitHub stack** | `create_github_api_service`, `create_pr_diff_service` | Reads `GitHubConfig` via `get_settings_service()` |
| **File processor / diffs** | `create_pr_diff_service` | Builds `FileProcessor` (`max_file_size_bytes`) + `DiffGenerator` (parallel thresholds) from `GitHubConfig` |
| **GitLab strict stack** | `create_gitlab_runtime`, `create_gitlab_session_reader` | Shared runtime limiter + session reader / VCS repo |
| **Process-wide factory** | `get_infrastructure_factory()` | Returns new `InfrastructureFactory()` |

## METHODS (HIGH LEVEL)
Settings, logger, cache, repository cache, GitHub API, diff utils, pattern matching, PR diff service (file processor + diff generator), input validator, **GitLab runtime + session reader**.

## CONVENTIONS
- Prefer **one authoritative `GitHubConfig`** when wiring clients/processors/services (no ad-hoc defaults that diverge from settings).
- Prefer **one authoritative `GitLabConfig`** + process-shared `GitLabRuntime` limiter when wiring GitLab.
- Parallel flags from config default **true** (settings + `GitHubConfig`):
  - `parallel_file_fetch_enabled`
  - `parallel_diff_generation_enabled`
- When parallel file fetch is disabled, the GitHub session limiter uses `github_worker_capacity` semantics (capacity 1).
- Diff generator receives `parallel_enabled` + `diff_max_workers` / `diff_parallel_threshold` from config.
- Return domain interfaces / concrete adapters as appropriate for callers.
- Lazy-import security validator to avoid circular imports where needed.

## ANTI-PATTERNS
- NO circular imports with application layer (application injects factory results; avoid re-entering carelessly).
- NO hardcoding timeouts/limits that already live on `GitHubConfig` / `GitLabConfig`.
- NO unbounded fan-out — always respect `max_concurrent` / `diff_max_workers`.
- NO creating a new process-wide GitLab limiter per request (reuse `create_gitlab_runtime`).
