# AGENTS.md - Application Unit Tests

MCP server, tools, components, factories, and application utilities.

## STRUCTURE
```
tests/unit/application/
├── components/                    # Auth, rate limit, metrics, health, PR ops, config
├── factories/                     # ApplicationFactory tests
├── utils/                         # PR URL parser tests
├── test_tool_registry.py          # MCP tools + GitHub/GitLab approve/describe (~1141); E5020 ToolError JSON
├── test_factory_gitlab_ops_wiring.py  # create_mcp_server dual-reader → ops auto-wire (~166)
├── test_webhook_handler.py
├── test_health_endpoints.py
├── test_mcp_server_health_status.py
├── test_pr_url_validation.py
├── test_logging_safety.py
└── test_architecture.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **MCP tools** | `test_tool_registry.py` | `get_pr_diff`, `approve_pr`, `describe_pr` (GitHub+GitLab); empty/whitespace reject; ops-not-configured metrics; nested NS; E5020 → ToolError JSON; `head_sha` in diff output; `expected_head_sha` validation + E1011 ToolError JSON |
| **Factory GitLab wiring** | `test_factory_gitlab_ops_wiring.py` | Dual-role reader auto-wires `gitlab_pr_operations`; explicit ops wins; reader-only leaves ops None |
| **PR URL multi-provider** | `utils/test_pr_url_parser.py` | `parse_pr_target` GitHub + GitLab (custom hosts) |
| **Auth / JWT / lockout** | `components/test_authentication.py` | Largest suite |
| **Webhooks** | `test_webhook_handler.py` | Cache invalidation orchestration |
| **Health / metrics HTTP** | `test_health_endpoints.py`, components | `/health`, metrics |
| **Layer checks** | `test_architecture.py` | Loads `scripts/analyze_dependencies.py` by path; real tree clean + tmp fixture trees prove engine and CLI reject every import form |

## CONVENTIONS
- Mock infrastructure ports and factories; focus on orchestration, auth gates, error translation — not domain math.
- Async tool paths: follow neighboring `@pytest.mark.asyncio` / anyio usage.
- Prefer `Mock(spec=...)` against domain Protocols where practical.

## ANTI-PATTERNS
- NO real HTTP server requirements for pure unit cases.
- NO live VCS API calls.
- NO business-logic assertions that belong in domain entity/use-case tests.
