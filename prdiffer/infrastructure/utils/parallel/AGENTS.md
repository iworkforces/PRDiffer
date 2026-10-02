# AGENTS.md - Parallel Execution

**Package:** 0.6.2  
anyio-based concurrent execution for bounded fan-out.

## STRUCTURE
```
prdiffer/infrastructure/utils/parallel/
├── executor.py     # AsyncParallelExecutor (~598)
├── results.py      # BatchResult, IndexedItemOutcome, IndexedBatchError (~124)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Executor** | `executor.py` | Task groups + per-batch semaphores; factories at bottom |
| **Indexed batch** | `execute_indexed_batch` | Identity-preserving full-diff fan-out |
| **Result types** | `results.py` | `ErrorStrategy`, `IndexedBatchResult`, `IndexedBatchError` |
| **Factories** | `get_async_parallel_executor`, `create_async_parallel_executor` | Singleton vs fresh |

## CONVENTIONS
- Use task groups + semaphores for bounded fan-out.
- Prefer this over thread pools for async I/O; use `anyio.to_thread` for blocking SDKs at the call site.
- **Per-batch semaphore**: each `execute_batch` / `execute_indexed_batch` creates `anyio.Semaphore(self.max_concurrent)` locally — **not** a long-lived instance field. Safe when one executor is reused across independent anyio event loops / native threads.
- **`execute_indexed_batch`**:
  - Outcomes keyed by submission index; returned in **input order**
  - Unique keys required
  - Strict mode cancels siblings and raises `IndexedBatchError` with **full ordered outcomes** — never a compacted success list
  - Prefer this for full-diff content fan-out (GitLab content fetch)
- Non-indexed helpers may use completion-order lists with `ErrorStrategy` (IGNORE/RAISE/COLLECT/CONTINUE) — not for identity-sensitive assembly.

## ANTI-PATTERNS
- NO unbounded concurrency against GitHub/GitLab APIs.
- NO mixing blocking SDK calls without `to_thread`/offload strategy.
- NO completion-order append for identity-sensitive full-diff work — use `execute_indexed_batch`.
- NO dropping failed-item identity when strict full-diff completeness is required.
- NO reusing a single anyio.Semaphore across different event loops (deadlock risk) — use per-batch construction.
