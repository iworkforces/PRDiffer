# AGENTS.md - Domain/Config

Configuration value objects and interfaces (no I/O). Package 0.6.2.

## STRUCTURE
```
prdiffer/domain/config/
├── github_config.py            # GitHubConfig frozen dataclass (~266)
├── github_config_interface.py  # GitHubConfigDict + GitHubConfigInterface Protocol (~108)
├── gitlab_config.py            # GitLabConfig frozen slotted VO (~129; limits + allowed_hosts)
├── mcp_server_config.py        # MCPServerConfig + parse_transport / parse_port (E5009)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **GitHub settings model** | `github_config.py` | Frozen; tuple fields for hashability; full-diff limits + parallel flags |
| **GitLab settings model** | `gitlab_config.py` | Frozen+slots; timeout, retries, capacity, size limits (no filters) |
| **Config interface** | `github_config_interface.py` | `@runtime_checkable` Protocol + TypedDict for DI |
| **Defaults** | `github_config.py` / `gitlab_config.py` top | Match `settings.toml` / plan contracts |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `GitHubConfig` | Frozen dataclass | `github_config.py` | Central GitHub settings VO |
| `GitLabConfig` | Frozen slotted dataclass | `gitlab_config.py` | GitLab strict-diff limits/resilience + host allowlist |
| `MCPServerConfig` | Frozen slotted dataclass | `mcp_server_config.py` | Validated MCP transport/host/port/path; stdio has `port=None` |
| `GitHubConfigInterface` | Protocol | `github_config_interface.py` | DI / typing surface |
| `GitHubConfigDict` | TypedDict | `github_config_interface.py` | `from_dict` / `with_overrides` keys |
| `github_worker_capacity` | property | `GitHubConfig` | 1 when `parallel_file_fetch_enabled` is false |

## FULL-DIFF FIELDS
| Field | Default | Notes |
|-------|---------|-------|
| `timeout` | 30 | Provider/GitHub SDK timeout (seconds) |
| `pr_diff_request_timeout_seconds` | 180.0 | Absolute request/coalescing deadline; must be `> timeout` |
| `max_file_size_bytes` | 10_485_760 (10 MiB) | Content size admission |
| `max_files_allowed` | 50 | Selected-file admission limit |
| `parallel_file_fetch_enabled` | `true` | GitHub session tree/blob capacity; 1 when off |
| `parallel_diff_generation_enabled` | `true` | Parallel ordered full-context generation |
| `max_concurrent` | 4 | Worker cap when parallel fetch enabled |

Also: retry/circuit-breaker knobs, `ignore_patterns` / `valid_extensions` as tuples, `diff_parallel_threshold` / `diff_max_workers` for parallel ordered generation.

### GitLabConfig fields
| Field | Default | Notes |
|-------|---------|-------|
| `timeout` | 30 | SDK client timeout (seconds); must be `< pr_diff_request_timeout_seconds` |
| `max_retries` | 3 | Transient 5xx retries (`>= 0`) |
| `max_concurrent` | 4 | Shared CapacityLimiter bound |
| `retry_transient_errors` | `true` | python-gitlab transient retry flag |
| `obey_rate_limit` | `true` | python-gitlab rate-limit obedience |
| `max_file_size_bytes` | 10_485_760 | Content size admission |
| `max_files_allowed` | 50 | From `app.max_files_allowed` when wired |
| `pr_diff_request_timeout_seconds` | 180.0 | From `mcp.pr_diff_request_timeout_seconds` when wired |
| `allowed_hosts` | `("gitlab.com",)` | Bare hostnames only; opt-in custom hosts via settings |

Helpers: `is_host_allowed(host)` (casefold; strips `:port`).

## CONVENTIONS
- Immutable config objects only; `__post_init__` validates positives and `timeout < pr_diff_request_timeout_seconds`.
- GitLab uses `ValueError` on invalid bounds; GitHub uses `ConfigurationError`; `MCPServerConfig` raises `ConfigurationError` E5009 naming the setting and its source (`--port`, `MCP_PORT`, `settings mcp.port`).
- Infrastructure loads Dynaconf and maps into these domain types (`from_dict` / `to_dict`).
- Never read env/files from this package.
- Helpers (GitHub): `should_ignore_file`, `has_valid_extension`, `should_process_file`, `with_overrides`.
- GitLabConfig has **no** ignore-pattern / extension filtering.
- Custom GitLab hosts require explicit `allowed_hosts` entries (no open-host default).
- Runtime overrides (infrastructure, not this package) via `SettingsService` / `.env` (see `.env.example`):
  - `GITLAB_ALLOWED_HOSTS` CSV for host allowlist
  - `MAX_FILES_ALLOWED` positive int for selected-file admission (`app.max_files_allowed`)
  - `GITHUB_IGNORE_PATTERNS` CSV replaces `github.ignore_patterns` (GitHub only)

## ANTI-PATTERNS
- NO Dynaconf / `os.environ` here.
- NO silent coercion of zero/negative limits.
- NO reusing `GitHubConfig` for GitLab.
- NO open/default-all host allowlist (SSRF with token).
- NO mutable config bags shared across threads without care.
- NO `@lru_cache` on settings construction (RLock manual cache only).
