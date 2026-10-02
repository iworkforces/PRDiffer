# AGENTS.md - Application/Factories

`ApplicationFactory` implements the domain factory interface (98 lines).

## STRUCTURE
```
prdiffer/application/factories/
├── application_factory.py  # ApplicationFactory + get_application_factory() (98)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Create components** | `application_factory.py` | rate limiter, metrics, PR ops, health, config, auth |
| **Singleton access** | `get_application_factory()` | Module-level singleton |

## METHODS
| Method | Returns (Protocol) |
|--------|--------------------|
| `create_rate_limiter` | `RateLimiterProtocol` |
| `create_metrics_tracker` | `MetricsTrackerProtocol` |
| `create_health_monitor` | `HealthMonitorProtocol` |
| `create_server_configuration` | `ServerConfigurationProtocol` |
| `create_authentication` | `AuthenticationProtocol` |

## CONVENTIONS
- Implements `ApplicationFactoryInterface` from domain.
- Return types are domain Protocols, not concrete component classes at the boundary.
- Construction should avoid global side effects where possible.
- `create_mcp_server()` in `application/factory.py` is the composition root (separate from this package); it is the known Application → Infrastructure import site.

## ANTI-PATTERNS
- NO embedding business logic in factory methods.
- NO registering MCP tools here (tools belong in `ToolRegistry`).
