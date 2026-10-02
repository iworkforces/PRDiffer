# AGENTS.md - Domain/Factories

Abstract factory contracts for dependency inversion (~133 lines). Package 0.6.2.

## STRUCTURE
```
prdiffer/domain/factories/
├── application_factory.py      # ApplicationFactoryInterface (~68)
├── infrastructure_factory.py   # InfrastructureFactoryInterface (~65)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **App component factory port** | `application_factory.py` | Rate limiter, metrics, auth, health, server config, PR ops |
| **Infra service factory port** | `infrastructure_factory.py` | Cache, GitHub API, retry, validator, PR diff service, … |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `ApplicationFactoryInterface` | ABC | `application_factory.py` | Create application-layer components |
| `InfrastructureFactoryInterface` | ABC | `infrastructure_factory.py` | Create infrastructure services |

### ApplicationFactoryInterface methods
- `create_rate_limiter` → `RateLimiterProtocol`
- `create_metrics_tracker` → `MetricsTrackerProtocol`
- `create_health_monitor` → `HealthMonitorProtocol`
- `create_server_configuration` → `ServerConfigurationProtocol`
- `create_authentication` → `AuthenticationProtocol`

### InfrastructureFactoryInterface methods
- `create_settings_service` → `SettingsServiceInterface`
- `create_logger_service` → `LoggerServiceInterface`
- `create_cache_service` → `CacheServiceInterface`
- `create_github_api_service` → `GitHubAPIServiceInterface`
- `create_diff_service` → `DiffServiceInterface`
- `create_pattern_matching_service` → `PatternMatchingServiceInterface`
- `create_pr_diff_service` → `SessionPRDiffReader`
- `create_input_validator` → `InputValidatorProtocol`

## CONVENTIONS
- Methods return interfaces/Protocols, not concrete classes.
- Implementations live in `application/factories/` and `infrastructure/factories/`.
- Application vs infrastructure creation is split (no app components on infra factory).

## ANTI-PATTERNS
- NO concrete infrastructure imports in domain factories.
- NO service construction with side effects here.
- NO returning concrete types that force domain → outer-layer coupling.
