# AGENTS.md - Domain/Services

Service interfaces (ABC) only — 7 ports. Package 0.6.2.

## STRUCTURE
```
prdiffer/domain/services/
├── cache.py                 # CacheServiceInterface (~64)
├── github_api.py            # GitHubAPIServiceInterface (~21) — client initialization only
├── diff.py                  # DiffServiceInterface (~27)
├── pattern_matching.py      # PatternMatchingServiceInterface (~35)
├── retry.py                 # RetryServiceInterface (~17)
├── settings.py              # SettingsServiceInterface (~63; get_github_config / get_gitlab_config)
├── logger.py                # LoggerServiceInterface + LogLevel (~32)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Add service port** | New `*.py` ABC here | Implement under infrastructure |
| **Snapshot-keyed cache** | `cache.py` | get/set with validation token; scoped GitHub invalidation |
| **Full-context patches** | `diff.py` | `build_full_file_patch` (+ `build_full_file_patch_chunked` default) |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `CacheServiceInterface` | ABC | `cache.py` | Snapshot-keyed PRDiff cache |
| `GitHubAPIServiceInterface` | ABC | `github_api.py` | `initialize_client` |
| `DiffServiceInterface` | ABC | `diff.py` | Full-file patches |
| `PatternMatchingServiceInterface` | ABC | `pattern_matching.py` | File filter/validation |
| `RetryServiceInterface` | ABC | `retry.py` | Retry with backoff |
| `SettingsServiceInterface` | ABC | `settings.py` | Config access (`get_github_config` / `get_gitlab_config`) |
| `LoggerServiceInterface` | ABC | `logger.py` | Logging contract |
| `LogLevel` | StrEnum | `logger.py` | DEBUG…CRITICAL |

## CONVENTIONS
- Abstract methods only; no default I/O.
- Implementations registered via `InfrastructureFactory`.
- The PR diff read port is the session Protocol in `domain/interfaces/pr_diff_reader.py` (`SessionPRDiffReader`), not a service ABC.

## ANTI-PATTERNS
- NO concrete classes with network/cache logic here.
- NO SDK types in method signatures.
- NO re-exports from `__init__.py`; import from the defining module.
