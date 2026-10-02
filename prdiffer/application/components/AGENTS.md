# AGENTS.md - Application/Components

Cross-cutting MCP components (~1.3K lines). Constructor DI + mixin composition.

## STRUCTURE
```
prdiffer/application/components/
├── authentication.py        # AuthenticationMiddleware (350) + AuthFailureRecord
├── jwt_handler.py           # JWTHandlerMixin (161)
├── api_key_manager.py       # APIKeyManagerMixin (136)
├── rate_limiter.py          # RateLimiter (136)
├── metrics_tracker.py       # MetricsTracker (145)
├── health_monitor.py        # HealthMonitor (99)
├── server_configuration.py  # ServerConfiguration (118)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **API key / lockout** | `authentication.py`, `api_key_manager.py` | SHA-256 keys, failure window, RLock |
| **JWT metadata** | `jwt_handler.py` | Metadata only unless verified path; not primary auth |
| **Rate limits** | `rate_limiter.py` | Per-client limits, thread-safe |
| **Metrics / health** | `metrics_tracker.py`, `health_monitor.py` | Request success rate, degraded thresholds |
| **Transport config** | `server_configuration.py` | Logging, MCP instructions, stdio/http/sse/streamable-http |

## CONVENTIONS
- Mixins for auth concerns; `AuthenticationMiddleware` composes `JWTHandlerMixin` + `APIKeyManagerMixin`.
- Inject `InputValidatorProtocol` when possible; infrastructure factory fallback is transitional.
- Sanitize logs (never log raw tokens/API keys).
- Thread safety: `threading.RLock` for sync shared state.
- Implement domain Protocols (`AuthenticationProtocol`, `RateLimiterProtocol`, etc.) for DI.

## ANTI-PATTERNS
- NO domain business logic (priority/smells/full-diff completeness) in components.
- NO trusting unverified JWT for authorization decisions (API keys are primary).
- NO unbounded failure-record maps (auth caps tracked clients).
- NO VCS SDK imports (PyGithub/python-gitlab).
