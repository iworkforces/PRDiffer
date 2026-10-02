# AGENTS.md - Application Component Unit Tests

6 modules, ~3.6K lines. Largest test file in the repo lives here.

## STRUCTURE
```
tests/unit/application/components/
├── test_authentication.py         # 1145 — largest suite (JWT, API keys, lockout)
├── test_metrics_tracker.py        # 564
├── test_rate_limiter.py           # 521
├── test_server_configuration.py   # 458
└── test_health_monitor.py         # 408
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| JWT / API key / lockout | `test_authentication.py` | Unverified JWT metadata only; API keys primary |
| Rate limit windows | `test_rate_limiter.py` | Window/max request behavior |
| Metrics counters | `test_metrics_tracker.py` | Request IDs, tracking |
| Health thresholds | `test_health_monitor.py` | Score / check_health |
| Server config validation | `test_server_configuration.py` | Config errors/logging |

## CONVENTIONS
- Inject mocks for validators and loggers.
- Cover thread-safety-sensitive paths with concurrent scenarios when modifying locks (`run_concurrently` fixture in root conftest when useful).
- Prefer controllable time over wall-clock for lockout/window tests.

## ANTI-PATTERNS
- NO depending on wall-clock alone for lockout tests without controllable time.
- NO using unverified JWT claims for auth decisions in production-facing assertions (metadata only).
- NO multi-second real sleeps.
