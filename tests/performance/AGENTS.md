# AGENTS.md - Performance Tests

Benchmark-style tests for hot paths, the strict-v1 full-diff harness, and GitLab runtime bounds.

## STRUCTURE
```
tests/performance/
├── test_performance.py                  # Component microbenchmarks
├── test_full_diff_benchmark.py          # strict-v1 harness validity + mode markers
└── test_gitlab_strict_full_diff.py      # CapacityLimiter bounds + per-call deadline E5004 (~91)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Harness matrix/modes** | `test_full_diff_benchmark.py` | Workload sizes, seed 5020, baseline vs post modes |
| **Fixture digests** | `test_full_diff_benchmark.py` | Determinism + preflight failures |
| **Overwrite refusal** | `test_full_diff_benchmark.py` | Sealed baseline artifacts |
| **Microbenches** | `test_performance.py` | Validator, auth, health tracker, pattern-matching, coalescing timing |
| **GitLab concurrency** | `test_gitlab_strict_full_diff.py` | max concurrent clients; deadline_monotonic on `run_blocking` |

## CONVENTIONS
- Full-diff harness lives in `scripts/bench_diff_generation.py`; tests load it by path (`importlib.util`, no `scripts` package required).
- Prefer tiny synthetic workloads inside unit tests; run the full matrix only for sealed baseline/post capture via the script.
- Use deterministic fixtures; never require network.
- Timing thresholds in `test_performance.py` leave headroom for slower machines.
- GitLab performance tests pass **per-call** `deadline_monotonic` (not ctor-shared state).

## ANTI-PATTERNS
- NO hard absolute timings that fail on slower hardware without margin.
- NO overwriting sealed baseline evidence from tests.
- NO claiming event-loop safety from the `async-current-negative-control` mode (`claims_event_loop_safety` is always false).
