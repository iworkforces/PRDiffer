# AGENTS.md - Application Layer

FastMCP server orchestration, tool registration, and cross-cutting components.

## OVERVIEW
**21** Python modules. Package **0.6.2**. Composition root wires tools, health, webhooks, and auth.

## STRUCTURE
```
prdiffer/application/
├── components/           # Auth (split), rate limit, metrics, health, config (7 modules)
├── factories/            # ApplicationFactory (~65)
├── utils/                # pr_url_parser (~104) — parse_pr_url, parse_pr_target, PRTarget
├── interfaces/           # Placeholder (__init__.py only)
├── plugins/              # Placeholder (AGENTS.md only; no Python)
├── services/             # Placeholder (AGENTS.md only; no Python)
├── mcp_server.py         # FastMCPServer (~181)
├── tool_registry.py      # ToolRegistry (~512) — get_pr_diff / approve_pr / describe_pr (GitHub+GitLab)
├── pr_diff_executor.py   # Coalesced PR diff execution (~62); host-aware coalesce key
├── health_endpoints.py   # HealthEndpoints (~120) — health MCP tool
├── webhook_handler.py    # WebhookHandler (~171)
├── startup_config.py     # StartupOverrides + resolve_mcp_server_config() (CLI > MCP_* env > settings > defaults)
└── factory.py            # create_mcp_server() (~107); wires gitlab_reader + auto gitlab_pr_operations
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Add MCP tool** | `tool_registry.py` | `@mcp.tool()` inside `ToolRegistry.register_tools()` |
| **Add component** | `components/*.py` | Constructor DI + domain Protocols |
| **Wire server** | `factory.py` | `create_mcp_server()` |
| **Lifecycle / transport** | `mcp_server.py` | Register tools, health tool, `/metrics`, `/webhook`; `run()` reads only the injected `MCPServerConfig` (network transports keep `uvicorn_config={"proxy_headers": False}`) |
| **Startup settings** | `startup_config.py` | Resolves and validates transport/host/port/path once; `create_mcp_server()` resolves before creating the logger or cache when no config is passed |
| **PR diff coalesce** | `pr_diff_executor.py` | Mixin used by `ToolRegistry`; `GetPRDiffUseCase` + request coalescing |
| **URL parse** | `utils/pr_url_parser.py` | `parse_pr_url` (GitHub only), `parse_pr_target` (GitHub/GitLab + `base_url`) |

## CONVENTIONS

### FastMCP tools
- Production tools live in **`ToolRegistry.register_tools()`** via `@mcp.tool()` — not under `plugins/`.
- **Inventory** (all accept GitHub PR + GitLab MR URLs except `health`):
  | Tool | Purpose | Provider-aware |
  |------|---------|----------------|
  | `get_pr_diff` | Strict full-context diff + snapshot `head_sha` | Yes |
  | `approve_pr` | Approve + non-empty compliment; optional `expected_head_sha` | Yes |
  | `describe_pr` | Update description body | Yes |
  | `health` | Health/metrics (via `HealthEndpoints`) | No |
- Routing for VCS tools: `parse_pr_target` → GitHub repository class or injected `GitLabPROperationsProtocol` (`base_url` for custom hosts).
- Composition: `create_mcp_server` may promote dual-role `gitlab_reader` to `gitlab_pr_operations` when ops are not passed explicitly (`_is_gitlab_pr_operations` TypeGuard).
- Empty/whitespace-only compliment or description → `ValidationError` (E1001) after `.strip()` without provider calls.
- `approve_pr` keeps `pr_url` + `compliment` as its only required inputs. An optional `expected_head_sha` is normalized with `require_git_object_sha` (40/64 hex) before any provider call (malformed → E1001) and forwarded through the approval capability. `HeadSHAMismatchError` becomes a `ToolError` with compact JSON `{"error_code":"E1011_HEAD_SHA_MISMATCH","message","details"}` and counts as an `approve_pr` failure.
- Failure metrics/logs use the real tool name via `operation=` on security/validation/runtime handlers.
- GitLab domain failures (E2006/E2007/E3006/E4001–E4003/E5021/E5004/E5019) bubble with original codes; unmapped `RuntimeError` (e.g. ops not configured) remaps to provider-neutral safe message + E5002.
- Custom routes: `GET /metrics`, `POST /webhook`.
- Async handlers; structured domain entities (`PRDiff`) as return types where applicable.

### Strict full-diff (`get_pr_diff`)
- **All-or-nothing full-context diffs**: successful responses include every selected file with path/status/stats and **generated full-context** unified `diff` text (not hunk-only provider patches).
- On incomplete reconstruction the tool fails with **`E5020_FULL_DIFF_INCOMPLETE`** and a stable `reason` — never a partial `files` array.
- Every success (including empty diffs and cache hits) returns `{files, head_sha}`, where `head_sha` is the open session's `snapshot.head_sha`; the `files` item schema is unchanged.
- At the FastMCP boundary, `FullDiffIncompleteError` becomes `ToolError` with compact JSON `{"error_code","message","details"}` (safe details only; no `files`).
- Routing: `parse_pr_target` → GitHub or GitLab (`base_url` forwarded into use case / session for custom hosts).

### Components
- Constructor DI: `FastMCPServer` and `ToolRegistry` require `input_validator` and `request_coalescing_service` (no infrastructure fallbacks); tests pass protocol fakes or real instances.
- Auth split: `authentication.py` + `jwt_handler.py` + `api_key_manager.py` mixins.
- Prefer domain Protocols (`prdiffer/domain/interfaces/protocols.py`, `input_validation`, `request_coalescing`) in type hints.

### Request pipeline
- Auth → rate limit → validate/sanitize → coalesce → execute → metrics.
- Coalesce keys include `base_url` for GitLab multi-host correctness.
- Webhooks invalidate repository/diff caches on relevant GitHub events (HMAC-verified).

## ARCHITECTURE NOTES
- Only `prdiffer/application/factory.py` (composition root) may import `prdiffer.infrastructure`, at any depth; it creates the input validator and request coalescer and injects them into `FastMCPServer` → `ToolRegistry`.
- `scripts/analyze_dependencies.py` (shared by CI and `test_architecture.py`) checks imports at every depth, including in-function and relative imports; a quick import inside a tool handler fails the gate.
- `parse_pr_url` / `parse_pr_target` require an explicit `InputValidatorProtocol`; pure GitLab MR URL parsing lives in `prdiffer/domain/entities/gitlab_merge_request_url.py`.
- MCP tools call repositories / `GitLabPROperationsProtocol` directly through write capabilities; empty/whitespace compliment and description are rejected at the tool boundary.
- MCP `get_pr_diff` resolves a `StrictDiffCapability` (session reader) via `ProviderCapabilityResolver`, then runs `GetPRDiffUseCase` under request coalescing (`pr_diff_executor.py`).

## ANTI-PATTERNS
- NO business rules that belong in domain entities/use cases.
- NO PyGithub/python-gitlab in this layer.
- NO production tool registration under empty `plugins/` (use `ToolRegistry`).
- NO inventing application service/plugin modules that are not in the tree.
- NO blocking I/O in tool handlers.
- NO returning partial PR diffs; incompleteness must surface as `E5020`.
- NO using `parse_pr_url` for GitLab (use `parse_pr_target`).
- NO hard-coding GitHub-only URL validation on `approve_pr` / `describe_pr` (provider dispatch required).
- NO trusting unverified JWT for auth decisions (API keys primary).
