# AGENTS.md - Infrastructure/Utils

**Package:** 0.6.2  
Resilience, parallelism, parsing, and shared utilities (including subpackages).

## STRUCTURE
```
prdiffer/infrastructure/utils/
├── retry/                      # Unified retry package (base, handler, models, factories)
├── parallel/                   # AsyncParallelExecutor (~598 in executor.py; per-batch semaphore)
├── circuit_breaker_core.py     # Canonical CircuitBreaker (~215)
├── coalescing_service.py       # Request coalescing
├── delay_calculator.py         # Backoff + jitter (~160)
├── error_classifier.py         # Retryability classification (~151)
├── rate_limit_parser.py        # Retry-After / rate headers (~183)
├── api_health_tracker.py       # Sliding window health (~131)
├── diff_utils.py               # DiffServiceInterface impl
├── pattern_matcher.py          # Ignore/extension patterns
├── url_parser.py               # GitHub PR + GitLab MR URL parsing (~281; custom hosts)
└── retry_logger.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Retry policy** | `retry/handler.py`, `retry/base.py` | Context-aware (skip file 404s) |
| **CB state machine** | `circuit_breaker_core.py` | `CircuitBreaker`, `CircuitState`, `CircuitBreakerOpenException` |
| **Fan-out** | `parallel/executor.py` | anyio task groups + per-batch semaphores |
| **Indexed identity** | `execute_indexed_batch` | Ordered outcomes; strict `IndexedBatchError` |
| **Coalesce** | `coalescing_service.py` | Deduplicate concurrent work |
| **Per-file diff line limit** | `diff_utils.py` | `max_diff_size` → E5020 `RESPONSE_SIZE_LIMIT` |
| **GitLab/GitHub URLs** | `url_parser.py` | `parse_github_*`, `parse_gitlab_merge_request_parts` (nested NS + host) |

## CONVENTIONS
- Prefer anyio over asyncio APIs in new code.
- Keep pure helpers free of domain orchestration.
- Import the defining module directly (`circuit_breaker_core.py`, `coalescing_service.py`, …); no re-export shim modules or packages.
- Owner cancel publishes terminal exception under a shielded scope so waiters wake and pending is cleared.
- Max-waiters overflow runs a **standalone** fetch and must not replace the pending owner entry.
- Parallel full-diff work must preserve identity/order via `execute_indexed_batch`.
- Strict indexed failures use **only** `IndexedBatchError.first_failure` (no second selection helper).
- Executor creates a fresh semaphore per batch (safe across independent anyio loops).
- Chunked large-file diffs apply Git `\ No newline at end of file` markers on the **last** hunk and emit **equal-context** chunks (full-context parity with the small-file path).

## ANTI-PATTERNS
- NO sleeping without jitter/caps on hot paths.
- NO shared mutable globals without locks.
- NO blind retry of all exceptions (especially content 404s).
- NO completion-order append for identity-sensitive full-diff batches.
- NO truncating diffs; per-file line-limit failures raise E5020.
- NO re-export shim modules/packages (e.g. `utils/coalescing/`, `utils/circuit_breaker/`, `utils/performance.py`).
