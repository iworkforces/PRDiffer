# AGENTS.md - Infrastructure Layer

**Package:** 0.6.2  
External integrations: GitHub/GitLab APIs, cache, security, resilience, DI, settings.

## OVERVIEW
**54** Python modules. Implements domain ports; owns I/O and third-party SDKs (PyGithub, python-gitlab, Dynaconf). Clean Architecture outer layer — depends on domain only.

## STRUCTURE
```
prdiffer/infrastructure/
├── cache/                      # CacheService, repository cache, keys, store
├── factories/                  # InfrastructureFactory (~234) — GitHub + GitLab wiring
├── github/                     # Full-diff path: client, inventory, git objects, file_processor, diff_generator, session
├── interfaces/                 # Empty reserved placeholder
├── logging/                    # ConsoleLogger, exception sanitization
├── security/                   # InputValidator, InjectionDetector, InputSanitizer
├── services/                   # GitHubPRDiffService (~194)
├── utils/                      # Retry, CB, parallel, coalescing, diff generation, URL, metrics
├── vcs_providers/              # GitLab strict pipeline + MR ops (gitlab_*.py)
├── github_repository.py        # GitHubPRDiffRepository — approve/describe adapter (~190)
├── github_repository_operations.py  # PR ops
├── github_repository_utils.py  # GitHub exception mapping helper
└── settings.py                 # SettingsService Dynaconf + RLock (GitHub + GitLab config) (~446)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **DI / singletons** | `factories/infrastructure_factory.py` | `InfrastructureFactory`; module-level `get_*_service()` singletons |
| **Wire services** | `factories/infrastructure_factory.py` | GitHubConfig + GitLabRuntime/session reader |
| **Settings** | `settings.py` → `GitHubConfig` / `GitLabConfig` | 30s provider / 180s request; host/file env overrides |
| **PR write adapter (GitHub)** | `github_repository.py` | Approve + describe (`GitHubPROperationsMixin`) |
| **Full-diff orchestration (GitHub)** | `services/pr_diff_service.py` | Maps `GeneratedFileDiff` → `FileDiffResponse`, session path |
| **GitHub API + content** | `github/` | Client (retry/CB), inventory, git tree/blob content, ordered processing |
| **GitLab strict full-diff** | `vcs_providers/gitlab_*.py` | Runtime, ops, inventory, content, assembler, session |
| **GitLab approve / describe** | `vcs_providers/gitlab_operations.py`, `gitlab_repository.py` | MR note-then-approve and description update for MCP tools |
| **GitHub URL parse** | `utils/url_parser.py` | `parse_github_pr_url`, `validate_github_pr_url` (GitLab MR parsing lives in `domain/entities/gitlab_merge_request_url.py`; allowlist stays in `vcs_providers/gitlab_runtime.py`) |
| **Retry** | `utils/retry/` | base / handler / models / factories |
| **Circuit breaker** | `utils/circuit_breaker_core.py` | State machine; one breaker per retry handler |
| **Parallel I/O** | `utils/parallel/executor.py` | ~598; per-batch semaphore; `execute_indexed_batch` |
| **Coalescing** | `utils/coalescing_service.py` | Deduplicate in-flight requests |
| **Cache** | `cache/service.py` | PRDiff snapshot cache (strict identity keys, TTL/LRU, webhook-scoped invalidation) |
| **Security** | `security/input_validator.py` | Orchestrates detector + sanitizer; GitHub + GitLab URL validation |
| **Per-file diff line limit** | `utils/diff_utils.py` | `diff.max_diff_size` overflow raises E5020 `RESPONSE_SIZE_LIMIT`; no truncation |

## CONVENTIONS

### Clean Architecture
- Implement domain interfaces/Protocols; map SDK types → domain entities at the boundary.
- No MCP/tool registration here (application layer).

### Resilience
- Retry + circuit breaker + optional API health tracker.
- **Never retry file-content 404s** (added/removed files).
- Exponential backoff with jitter via `delay_calculator.py`.

### Async
- anyio-first; `AsyncParallelExecutor` for fan-out (fresh semaphore per batch — loop-safe reuse).
- GitHub session work (tree/blob reads) runs on worker threads under one per-reader `CapacityLimiter`.
- Request coalescing and PR sessions use anyio primitives (`to_thread`, CapacityLimiter).
- **GitLabRuntime.run_blocking**: process-shared limiter; per-call `base_url` + `deadline_monotonic`; `abandon_on_cancel=False`; wall-clock deadline check after worker.

### Configuration
- Authoritative GitHub config: `SettingsService.get_github_config()` → frozen `GitHubConfig`.
- Authoritative GitLab config: `SettingsService.get_gitlab_config()` → frozen slotted `GitLabConfig`.
  - Priority for allowlist: `GITLAB_ALLOWED_HOSTS` env (CSV) → `settings.toml` `gitlab.allowed_hosts` → default `gitlab.com`.
  - Priority for file admission: `MAX_FILES_ALLOWED` env → `gitlab.max_files_allowed` / `app.max_files_allowed` → default `50`.
  - Priority for GitHub ignore list: `GITHUB_IGNORE_PATTERNS` env (CSV, replaces) → `settings.toml` `github.ignore_patterns`.
- Manual settings cache with `RLock` (Dynaconf unhashable → no `@lru_cache`); `clear_cache` drops GitHub and GitLab config caches.
- Parallel performance flags default **true** (bounded by `max_concurrent` / `diff_max_workers`).

### Imports
No re-export shims: import the defining module (`utils/circuit_breaker_core.py`,
`cache/service.py`, `utils/coalescing_service.py`). Package `__init__.py`
files hold docstrings only.

### Full-diff hard fails
- Inventory / admission / content / generation / size failures raise `FullDiffIncompleteError` → **E5020**.
- GitLab: pin exactly one MR diff version matching `diff_refs`; equal-content equal-mode modified is hard E5020.
- Unexpected algorithm defects may surface as E5003.

## ANTI-PATTERNS
- NO leaking SDK types (PyGithub/python-gitlab) into domain entities or MCP tools.
- NO `@lru_cache` on Dynaconf-backed settings.
- NO bypassing retry/CB for GitHub rate limits without reason.
- NO logging secrets or raw tokens.
- NO unbounded file downloads / unbounded parallel fan-out against VCS APIs.
- NO truncating full-diff public content; incomplete results hard-fail with E5020.
- NO shared mutable request deadline/base_url on process-wide `GitLabRuntime`.
- NO open host + token SSRF — always `ensure_host_allowed` before client create.
- NO blocking python-gitlab on the event loop (always `run_blocking` / `to_thread`).
