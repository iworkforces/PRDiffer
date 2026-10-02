# AGENTS.md - Infrastructure/Cache

**Package:** 0.6.2  
In-process PRDiff cache keyed by strict session snapshot identity, with TTL/LRU eviction.

## STRUCTURE
```
prdiffer/infrastructure/cache/
├── service.py              # CacheService — PRDiff snapshot cache
└── __init__.py             # Docstring only (no re-exports)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **PRDiff cache** | `service.py` | `get_cache_service()` singleton; strict `github-full-diff-v3` / `gitlab-full-diff-v1` keys; optional key hashing |
| **Webhook invalidation** | `service.py` | `invalidate_github_pr` / `invalidate_github_repository` parse strict v3 keys only |
| **Health stats** | `service.py` | `get_stats()` feeds the `health` tool `cache` section |

## CONVENTIONS
- Import `prdiffer.infrastructure.cache.service` directly; there are no shim subpackages.
- Thread-safe access (async lock); support invalidation on webhook events.
- Snapshot-identity keys only; any other key format misses.

## ANTI-PATTERNS
- NO unbounded growth without eviction/TTL.
- NO caching secrets or raw auth tokens.
- NO caching unavailable file-content sentinels.
- NO re-export shim packages.
