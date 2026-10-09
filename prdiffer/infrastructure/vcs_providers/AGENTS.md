# AGENTS.md - VCS Providers

**Package:** 0.6.2  
GitLab adapters: the strict full-diff pipeline (session reader) plus MCP approve/describe mutations. The GitHub adapters live in `infrastructure/github*`.

## STRUCTURE
```
prdiffer/infrastructure/vcs_providers/
├── gitlab_repository.py     # GitLabVCSRepository session + MR approve/describe (~90)
├── gitlab_models.py         # Diff refs / version / snapshot / record models (~167)
├── gitlab_inventory.py      # State/cardinality admission + edit classification (~198)
├── gitlab_content.py        # Ref-pinned content; tree for symlink/gitlink; first_failure
├── gitlab_objects.py        # Immutable tree/blob helpers (no submodule traversal)
├── gitlab_diff_generator.py # Ordered full-context FileDiffResponse assembly (~149)
├── gitlab_diff_session.py   # Request-scoped session + SessionPRDiffReader (~197)
├── gitlab_operations.py     # Stateless version pin + approve/describe on a provided client (~301)
├── gitlab_runtime.py        # Bounded SDK runner + status mapper (~390)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **GitHub PR write adapter** | `../github_repository.py` | Sibling module, not this package |
| **GitLab adapter** | `gitlab_repository.py` | `open_pr_diff_session` (SessionPRDiffReader) + async `approve_pr_with_comment` / `update_pr_description` |
| **GitLab models** | `gitlab_models.py` | `GitLabDiffSnapshot`, `GitLabDiffRecord`, version/refs |
| **GitLab inventory** | `gitlab_inventory.py` | Admit/classify; `a_mode`/`b_mode` `"0"`/`"000000"` = absent side on add/delete |
| **GitLab runtime** | `gitlab_runtime.py` | Shared CapacityLimiter; per-call base_url/deadline; status map |
| **GitLab ops (diff)** | `gitlab_operations.py` | Pin exact MR diff version matching `diff_refs` (`select_with_client`) |
| **GitLab ops (MR tools)** | `gitlab_operations.py` | `approve_with_client` (**note then approve**; optional `expected_head_sha`), `update_description_with_client` |
| **Session** | `gitlab_diff_session.py` | open → pin via `run_blocking` → build inventory/content/assemble → aclose |
| **Register provider** | `application/provider_resolver.py` | URL parser + capability registration (`create_provider_capability_resolver`) |

## CONVENTIONS
- Unified-diff stats: count `+`/`-` only inside `@@` hunks (source `++`/`--` payloads count).
- `real_size` parse rejects booleans/floats/negatives; only nonnegative int or digit string.
- Map provider models → domain entities at the boundary (`FileDiffResponse`, `PRDiff`).
- Use shared retry/security utilities where applicable.
- URL detection: GitHub.com PR paths vs any HTTPS host with `/-/merge_requests/` (custom GitLab).
- GitLab renames set `FileDiffResponse.previous_path` from `old_path` when distinct; no retrieval redesign.
- Expose capabilities through domain Protocols (`SessionPRDiffReader`, `GitLabPROperationsProtocol`); routing lives in `ProviderCapabilityResolver`.
- **GitLabRuntime**: process-shared limiter only; **per-call** `base_url` + `deadline_monotonic` (never shared mutable request state); each `run_blocking` uses a fresh client closed in `finally`; `abandon_on_cancel=False`; post-worker wall-clock deadline → E5004; inject `max_retries`/`obey_rate_limit` via `http_request` defaults.
- **Host allowlist**: `GitLabConfig.allowed_hosts` (default `gitlab.com`); env `GITLAB_ALLOWED_HOSTS` CSV; `ensure_host_allowed` rejects non-allowlisted hosts before SDK (E1001). `GitLabOperations` never opens clients; every SDK client comes from `GitLabRuntime`.
- **Tree completeness**: `load_repository_tree_entries` requires full pagination (`get_all=True`) and fails closed with `INVENTORY_TRUNCATED` when `next_page` remains set.
- **Session open**: pin snapshot via `runtime.run_blocking(ops.select_with_client, base_url=…, deadline=…)` — never call python-gitlab on the event loop.
- **Content fetch**: forward per-request `base_url` + `deadline_monotonic` into every raw content `run_blocking`.
- **Cache identity**: host from `cache_host_from_base_url` (port-aware for non-80/443).
- **Equal-noop**: equal-content equal-mode modified → hard E5020 `DIFF_GENERATION_FAILED`.
- Status map: 401→E2006, 403→E2007, 404→context (E4001/E4002/E4003), 429→E3006 (no local re-loop), 5xx→E5021, timeout→E5004, connection→E5019.
- **Approve MCP path**: `notes.create({body})` **then** `merge_request.approve()` (note-first so a note failure cannot leave the MR approved while the tool errors); empty/whitespace body → ValidationError E1001 before SDK.
- **Head-bound approve** (`expected_head_sha` supplied): read the fresh MR's `sha` (not `diff_refs`) before the note; a missing/malformed head → E5021, a different head → `HeadSHAMismatchError` (E1011) with no note. Then `notes.create` and `approve(sha=expected)` run as single attempts (`retry_transient_errors=False`, `max_retries=0`, `obey_rate_limit=False`). An approval 409 → E1011 after deleting only the note this call created (`compliment_note` = `deleted` / `cleanup_failed`); timeouts and other ambiguous failures go through the normal mapping with no cleanup and no retry. Without the SHA the path is unchanged.
- **Describe MCP path**: set `description` + `save()`; empty/whitespace → ValidationError E1001 before SDK.
- Async repository methods always use `GitLabRuntime.run_blocking` (never block the event loop).
- Dual role: `GitLabVCSRepository` is both `SessionPRDiffReader` and `GitLabPROperationsProtocol` (factory may promote reader → ops).

## ANTI-PATTERNS
- NO provider-specific types leaking to application tools.
- NO live API calls in unit tests.
- NO sharing SDK client objects across workers.
- NO second 429 retry loop outside SDK policy.
- NO `response_body` / tokens in mapped exception details.
- NO provider routing outside `ProviderCapabilityResolver` (no parallel registry).
- NO shared request deadline/base_url on process-wide `GitLabRuntime` instance state.
- NO calling python-gitlab on the event loop (always `run_blocking` / `to_thread`).
- NO open host + token SSRF: always enforce `allowed_hosts` before client create.
- NO partial full-diff success or silent equal-noop modified records.
