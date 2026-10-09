# AGENTS.md - Tests

pytest suite: unit, integration, performance, root phase/client regression tests.

## OVERVIEW
- **128** Python files under `tests/`
- **~1874** `test_*` functions across **113** `test_*.py` files
- Package under test: **prdiffer 0.6.2**
- Shared fixtures: `tests/conftest.py` (auto env + singleton reset)
- Largest suite remains under `unit/application/components/` (auth)

## STRUCTURE
```
tests/
├── conftest.py                      # Markers, mocks, sample entities, auto-use env/singletons
├── test_cache_hashing.py            # Cache key hashing
├── test_phase{1-4}_improvements.py  # Historical phase regression suites
├── unit/
│   ├── domain/                      # Entities, use cases, errors, strict cache identity, config
│   ├── infrastructure/              # GitHub, GitLab (incl. vcs_providers/), cache, utils, DI, security, settings
│   ├── application/                 # Tools, components, webhooks, health, factory GitLab ops wiring
│   └── test_version_consistency.py / test_server_gitlab_composition.py
├── integration/                     # Workflows, security, webhooks, MCP surface, GitLab strict, launcher, optional real API
└── performance/                     # Microbenches + full-diff harness + GitLab capacity/deadline
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Domain purity / entities** | `unit/domain/` | E5020, cache identity, session use case, GitLabConfig |
| **Retry / CB / cache utils** | `unit/infrastructure/utils/` | Circuit breaker, retry, coalescing, cross-loop executor |
| **GitHub adapters / full-diff** | `unit/infrastructure/github/` | Inventory, git tree/blob content, ordered processor, generator, session |
| **GitLab strict full-diff** | `unit/infrastructure/vcs_providers/`, `test_gitlab_*.py` | Runtime, session, assembler, inventory |
| **GitLab MR approve/describe** | `unit/infrastructure/vcs_providers/test_gitlab_mr_operations.py`, `test_gitlab_approval_retry_policy.py` | Note-then-approve, empty body, error map, nested path, custom host; head-bound pre-note mismatch, 409 cleanup, single-attempt SDK mutations |
| **PR diff service** | `unit/infrastructure/` | `test_pr_diff_service*`, concurrency defaults |
| **MCP tools / auth** | `unit/application/` | Tool registry (GitHub+GitLab dispatch, E5020 / E1011 ToolError JSON), components |
| **Factory GitLab ops wiring** | `unit/application/test_factory_gitlab_ops_wiring.py` | Auto-wire reader → `gitlab_pr_operations` |
| **Strict MCP surface** | `integration/test_full_diff_mcp_surface.py` | In-process FastMCP (`get_pr_diff`) |
| **GitLab integration** | `integration/test_gitlab_strict_full_diff.py` | No-network session + cache identity |
| **Launcher token gate** | `integration/test_server_launcher.py` | Shell entry; isolated `ENV_FILE` |
| **MCP startup config** | `integration/test_server_startup_config.py`, `unit/application/test_startup_config.py`, `unit/domain/config/test_mcp_server_config.py` | Precedence + E5009 fail-fast through real `main()` |
| **E2E-ish flows** | `integration/` | Workflow, security, webhooks |
| **Full-diff bench validity** | `performance/test_full_diff_benchmark.py` | Loads `scripts/bench_diff_generation.py` |
| **GitLab capacity/deadline** | `performance/test_gitlab_strict_full_diff.py` | Runtime limiter + E5004 |

## MARKERS
Registered in `conftest.pytest_configure` (and partially in `pyproject.toml`):
- `unit` — isolated, no external I/O
- `integration` — cross-component / optional external
- `slow` — slow-running
- `security` — security / vulnerability paths
- `thread_safety` — concurrency / lock paths

`pyproject.toml` `[tool.pytest.ini_options]`: `asyncio_mode = "auto"`, `--strict-markers`, `testpaths = ["tests"]`.

## CONVENTIONS
- Unit tests mock all network I/O (PyGithub, python-gitlab, httpx).
- Prefer domain interfaces in mocks (`Mock(spec=...)`).
- **Async**: production is anyio-first; tests largely use `@pytest.mark.asyncio` (pytest-asyncio). Some modules use `@pytest.mark.anyio`. Follow neighboring tests in the same package.
- Auto-use fixtures: `set_test_environment` (`ENV_FOR_DYNACONF=testing`, dummy tokens), `reset_singletons` (cache/settings/logger).
- GitLab allowlist tests may set `GITLAB_ALLOWED_HOSTS` via monkeypatch.
- GitHub content tests use tree-capable fakes (`get_git_tree` / `get_git_blob`) and assert order, modes, and fail-closed E5020.
- Live API suite `integration/test_real_github_api.py` is **always skipped** (`skipif(True)`); unit tests never require network.
- CI: `.github/workflows/pr-quality.yml` runs `ruff check`, `ty check`, `scripts/analyze_dependencies.py --path prdiffer`, `pytest tests` on PRs to `main`/`develop` (`uv sync --frozen --group dev`).

## COMMANDS
```bash
./start-unittest.sh --run
./start-unittest.sh --coverage
./start-unittest.sh --parallel
./start-unittest.sh --file tests/unit/domain/test_exceptions.py
./start-unittest.sh --pattern test_gitlab
uv run pytest tests -v --tb=short
uv run pytest tests -m unit
uv run pytest tests -m "not slow"
uv run pytest tests/unit/infrastructure/vcs_providers/ -v
uv run pytest tests/unit/infrastructure/github/ -k file_processor -v
```

## ANTI-PATTERNS
- NO live GitHub/GitLab tokens required for unit tests.
- NO putting integration tests under `unit/`.
- NO asserting on third-party SDK internals beyond our wrappers.
- NO multi-second sleeps in unit tests (patch timers / use short delays).
- NO production logic that exists only in tests.
