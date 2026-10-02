# AGENTS.md - Infrastructure Utils Unit Tests

12 test modules, ~4.3K lines.

## STRUCTURE
```
tests/unit/infrastructure/utils/
├── test_circuit_breaker.py                        # 675
├── test_retry_handler_comprehensive.py            # 513
├── test_retry_handler.py                          # 199
├── test_diff_utils.py                             # 429
├── test_rate_limit_parser.py                      # 424
├── test_error_classifier.py                       # 396
├── test_pattern_matcher.py                        # 333
├── test_delay_calculator.py                       # 298
├── test_coalescing.py                             # 203
└── test_async_parallel_executor_cross_loop.py     # 96 — per-batch semaphore across loops
```

Also: `tests/unit/infrastructure/test_async_parallel_executor.py` (~832) lives one level up and covers the main executor contract.

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| **CB state machine** | `test_circuit_breaker.py` | CLOSED → OPEN → HALF_OPEN → CLOSED |
| **Retry + CB integration** | `test_retry_handler*.py` | Context-aware retry |
| **Error classification** | `test_error_classifier.py` | 404 file content not retried |
| **Diff helpers** | `test_diff_utils.py` | Diff generation utilities |
| **Request coalescing** | `test_coalescing.py` | In-flight request sharing |
| **Cross-loop executor** | `test_async_parallel_executor_cross_loop.py` | Executor reused across independent anyio loops |
| **Main executor** | `../test_async_parallel_executor.py` | Indexed batch, strategies, capacity |

## CONVENTIONS
- Full CB cycle: CLOSED → OPEN → HALF_OPEN → CLOSED.
- Deterministic backoff: patch randomness / use short delays.
- Assert file-content 404s are not retried.
- CB lives in `circuit_breaker_core.py`; import it directly.
- Cross-loop semaphore safety: spawn-process / dual-thread anyio.run patterns; mark `thread_safety` when appropriate.

## ANTI-PATTERNS
- NO multi-second sleeps in unit tests.
- NO live network or real rate-limit waits.
