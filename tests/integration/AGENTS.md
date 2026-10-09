# AGENTS.md - Integration Tests

End-to-end oriented tests (workflows, security, MCP surface, GitLab strict, optional live API).

## STRUCTURE
```
tests/integration/
├── test_complete_workflow.py        # Full tool workflow
├── test_error_scenarios.py          # Error paths
├── test_security.py                 # Security integration
├── test_webhook_invalidation.py     # Cache invalidation
├── test_full_diff_mcp_surface.py    # Strict full-diff FastMCP surface (E5020 ToolError JSON)
├── test_github_strict_full_diff.py  # No-network GitHub session pipeline matrix
├── test_gitlab_strict_full_diff.py  # No-network GitLab session + cache identity (~244)
├── test_real_github_api.py          # Always-skipped live API suite (skipif True)
├── test_server_launcher.py          # Process/launcher (token gate; isolated ENV_FILE)
├── test_server_startup_config.py    # Real main(): precedence, Dynaconf envs, E5009 fail-fast
├── test_metrics_endpoint.py         # Metrics
└── mcp_server_manual_test.py        # Manual harness helper
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Strict full-diff MCP contract** | `test_full_diff_mcp_surface.py` | In-process FastMCP; success + E5020; rename `previous_path` (get_pr_diff) |
| **GitHub strict path** | `test_github_strict_full_diff.py` | Fake PyGithub surface; mixed statuses; v3 cache; drift/E5020 |
| **GitLab strict path** | `test_gitlab_strict_full_diff.py` | FakeOps + FakeClient; ordered multi-status; cache host identity |
| **Tool workflow** | `test_complete_workflow.py` | End-to-end tool orchestration with mocks |
| **Attack / injection paths** | `test_security.py` | Marked `integration` |
| **Webhook cache bust** | `test_webhook_invalidation.py` | Invalidation + error bodies |
| **Launcher / tokens** | `test_server_launcher.py` | GitHub-only / GitLab-only / both / neither; sets `ENV_FILE` to avoid real `.env` |
| **Startup config** | `test_server_startup_config.py` | Patches `project_root`, sets `ROOT_PATH_FOR_DYNACONF` + cwd to `tmp_path`; records `FastMCP.run` (nothing binds) |
| **Live GitHub (disabled)** | `test_real_github_api.py` | Module-level `skipif(True)` — never runs in CI/local by default |

## CONVENTIONS
- Prefer fakes/mocks unless explicitly running real API tests.
- Mark with `@pytest.mark.integration` (and `@pytest.mark.anyio` where async FastMCP surface needs it).
- Keep secrets out of fixtures; use env only for opt-in real API runs.
- Default local/CI path must not require network.
- GitLab fakes must implement `select_with_client` (the session path pins versions via `GitLabRuntime.run_blocking`).

## ANTI-PATTERNS
- NO committing tokens or API keys.
- NO assuming network in default CI or local runs.
- NO placing pure unit cases here when they belong under `tests/unit/`.
