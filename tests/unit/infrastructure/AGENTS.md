# AGENTS.md - Infrastructure Unit Tests

Adapters, DI, cache, GitLab/GitHub, security, settings, full-diff plumbing.

## STRUCTURE (HIGH LEVEL)
```
tests/unit/infrastructure/
├── github/                              # Client, tree processor, generator, session, inventory
├── vcs_providers/                       # GitLab runtime/session/assembler/models + MR approve/describe
├── utils/                               # Retry, CB, cache decorator, parsers, cross-loop executor
├── cache/                               # Cache store/keys/decorators + repository/
├── security/                            # Detector, sanitizer, helpers
├── logging/                             # Exception utils
├── factories/                           # InfrastructureFactory
├── test_github_repository.py
├── test_gitlab_*.py                     # config wiring, ops, content, pagination, VCS provider
├── test_pr_diff_service.py
├── test_pr_diff_service_comprehensive.py
├── test_pr_diff_service_full_context.py # Strict full-context PRDiff mapping
├── test_pr_diff_service_updates.py
├── test_github_config_wiring.py         # GitHubConfig defaults through settings/factory
├── test_gitlab_config_wiring.py         # GitLabConfig + GITLAB_ALLOWED_HOSTS env override
├── test_full_diff_concurrency_defaults.py
├── test_async_parallel_executor.py      # anyio parallel executor (~832)
├── test_settings_*.py
├── test_request_coalescing.py
├── test_input_validator.py
├── test_url_parser.py                   # GitHub + GitLab URL parse (custom hosts)
└── …
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Full-diff config defaults** | `test_github_config_wiring.py`, `test_full_diff_concurrency_defaults.py` | Parallel flags, capacity |
| **GitLab config / allowlist** | `test_gitlab_config_wiring.py` | toml defaults + `GITLAB_ALLOWED_HOSTS` |
| **GitLab runtime / session** | `vcs_providers/test_gitlab_*.py` | Per-call deadline/base_url, allowlist, equal-noop |
| **GitLab approve / describe** | `vcs_providers/test_gitlab_mr_operations.py` | Note-then-approve order, empty body, 401/403/429/404/5xx, nested path + custom host runtime |
| **Service full-context** | `test_pr_diff_service_full_context.py` | Generated full-context → PRDiff |
| **Parallel executor** | `test_async_parallel_executor.py` | anyio task groups, ordered batches, per-batch semaphore |
| **GitHub adapter details** | `github/` | See package AGENTS.md |
| **Resilience** | `utils/` | Retry + circuit breaker |

## CONVENTIONS
- Mock PyGithub / python-gitlab / httpx.
- Prefer anyio-compatible async tests consistent with neighboring files (`@pytest.mark.asyncio` or `@pytest.mark.anyio`).
- Cover retry classification, rate limits, and circuit breaker transitions when touching resilience code.
- Never retry 404s for file content in retry/error-classifier tests.
- GitLab session tests mock `runtime.run_blocking` around `select_with_client`.

## ANTI-PATTERNS
- NO live API calls.
- NO integration-only scenarios that belong under `tests/integration/`.
- NO multi-second sleeps (patch delays / short timeouts).
