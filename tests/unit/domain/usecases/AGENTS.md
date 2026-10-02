# AGENTS.md - Domain Use Case Unit Tests

5 test modules, ~790 lines.

## STRUCTURE
```
tests/unit/domain/usecases/
├── test_session_pr_diff_usecase.py    # Session open → identity → cache/build → aclose
└── test_pr_diff_usecases_purity.py    # AST import purity checks
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| **Session PRDiff** | `test_session_pr_diff_usecase.py` | Session build/aclose, cache hit/miss, error closure |
| **Domain isolation** | `test_pr_diff_usecases_purity.py` | No `prdiffer.application` imports |

## CONVENTIONS
- Mock service/repository interfaces only.
- `*_purity` tests guard domain isolation regressions (AST walk of use case modules).
- Async use cases: use asyncio.run or pytest-asyncio consistent with neighbors.

## ANTI-PATTERNS
- NO real provider clients.
- NO infrastructure factory wiring in use case unit tests.
