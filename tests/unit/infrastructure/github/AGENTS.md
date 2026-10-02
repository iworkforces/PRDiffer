# AGENTS.md - GitHub Infrastructure Unit Tests

14 files, ~3.6K lines. Core full-diff pipeline coverage.

## STRUCTURE
```
tests/unit/infrastructure/github/
├── test_api_client_comprehensive.py           # ~519
├── test_file_processor_comprehensive.py       # ~587
├── test_file_processor_ordered.py             # ~112 — ordered strict assembly
├── test_diff_generator.py                     # ~101
├── test_diff_generator_comprehensive.py       # ~770
├── test_generated_file_diffs.py               # ~206 — ordered full-context generation
├── test_inventory_admission.py                # ~91 — authoritative inventory + admission
└── test_pr_diff_session.py                    # session v3 identity / merge-base capture / revalidate / CapacityLimiter
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| **Inventory / 3000 cap** | `test_inventory_admission.py` | `INVENTORY_TRUNCATED`, admission selection |
| **Ordered processor** | `test_file_processor_ordered.py` | Strict assembly, sync/async parity |
| **Generated full-context diffs** | `test_generated_file_diffs.py` | Ordered diffs, E5003 mapping |
| **PR diff session** | `test_pr_diff_session.py` | anyio limiter, aclose, GitHub v3 merge-base `cache_identity` |
| **Happy vs edge** | `*_comprehensive.py` | Edge/error branches |

## CONVENTIONS
- Basic vs comprehensive split: happy path vs edge/error branches.
- Mock `github.Github` hierarchy; assert our wrapper behavior.
- Full-diff incompleteness must surface as `FullDiffIncompleteError` with a reason (never silent truncation).
- File processor tests use `TreeRepo`-style fakes serving `get_git_tree` / base64 `get_git_blob`.
- Session tests may use `@pytest.mark.anyio` + anyio primitives.

## ANTI-PATTERNS
- NO real GitHub network.
- NO treating 404 file content as retryable transient errors.
- NO asserting partial success after operational failure.
