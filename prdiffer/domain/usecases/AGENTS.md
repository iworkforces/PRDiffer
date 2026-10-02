# AGENTS.md - Domain/Use Cases

Thin business orchestration over injected ports. Package 0.6.2.

## STRUCTURE
```
prdiffer/domain/usecases/
├── pr_diff_usecases.py          # GetPRDiffUseCase (~60)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Fetch structured diff** | `pr_diff_usecases.py` | Session path (GitHub + GitLab) |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `GetPRDiffUseCase` | Use case | `pr_diff_usecases.py` | open → `cache_identity` → cache/build → aclose; optional `base_url` |

## SESSION FLOW (GetPRDiffUseCase)
1. Open one session via `SessionPRDiffReader.open_pr_diff_session` (always passes `base_url`; GitHub ignores it, GitLab uses it for custom hosts).
2. Cache via `session.cache_identity` (provider-neutral key + validation token); read via `unwrap_pr_diff_cache_value` (exact identity key, bare `PRDiff` only).
3. On miss: `session.build_pr_diff()`, store under the identity key; always `session.aclose()` in `finally`.

## CONVENTIONS
- Constructor-inject interfaces only (`SessionPRDiffReader` / `CacheServiceInterface` / `PRDiffRepositoryInterface`).
- No framework, auth, or HTTP concerns (those live in application tools).
- Keep use cases short; push provider details to infrastructure.

## ANTI-PATTERNS
- NO direct VCS SDK usage.
- NO caching/retry implementation details beyond port calls.
- NO skipping `aclose` on the session path.
- NO writing non-`PRDiff` values under strict GitHub-v3 / GitLab-v1 keys.
