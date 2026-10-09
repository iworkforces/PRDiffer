# AGENTS.md - Infrastructure/Logging

**Package:** 0.6.2  
Console logging and exception formatting for safe diagnostics (~431 lines).

## STRUCTURE
```
prdiffer/infrastructure/logging/
├── console_logger.py     # ConsoleLogger + get_logger (147)
├── exception_utils.py    # Safe exception serialization / redaction (284)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Logger singleton** | `console_logger.py` | `get_logger(transport=None)` → `ConsoleLogger`; a given transport picks the stream and switches an existing singleton via `use_transport()` |
| **Sanitize exceptions** | `exception_utils.py` | `sanitize_exception_for_logging`, auth-header redaction |
| **Domain port** | domain `LoggerServiceInterface` | Implemented by ConsoleLogger |

## CONVENTIONS
- Implement `LoggerServiceInterface` from domain.
- Never log tokens, API keys, or full secrets.
- Use structured fields where available.
- Keep **stdio MCP transport** safe: diagnostics must not corrupt primary JSON-RPC on stdout.
- Prefer `sanitize_exception_for_logging` before logging caught SDK/errors.

## ANTI-PATTERNS
- NO `print` debugging on library paths that can break stdio MCP.
- NO logging raw Authorization headers or tokens.
- NO dumping full stack traces with secrets into shared logs without redaction.
