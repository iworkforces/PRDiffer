# AGENTS.md - Domain/Interfaces

Cross-cutting ports and Protocols (~560 lines). Package 0.6.2.

## STRUCTURE
```
prdiffer/domain/interfaces/
├── pr_diff_reader.py        # SessionPRDiffReader, PRDiffSnapshot, cache_identity
├── protocols.py             # Application component Protocols (~210)
├── input_validation.py      # InputValidatorProtocol (~121)
├── request_coalescing.py    # RequestCoalescingProtocol (~50)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Strict session path** | `pr_diff_reader.py` | `SessionPRDiffReader.open_pr_diff_session` + `cache_identity` |
| **Component typing** | `protocols.py` | Auth, rate limit, metrics, health, config, GitLab MR ops |
| **Security port** | `input_validation.py` | Injected into auth / tools |
| **Coalescing port** | `request_coalescing.py` | Deduplicate concurrent PR fetches |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `PRDiffSnapshot` | Frozen dataclass | `pr_diff_reader.py` | owner/repo/pr + base_tip + merge_base + head + file count |
| `require_git_object_sha` | Helper | `pr_diff_reader.py` | 40/64-hex SHA validation (GitHub open path) |
| `require_changed_files_count` | Helper | `pr_diff_reader.py` | Non-boolean nonnegative int |
| `PRDiffReadSessionInterface` | Protocol | `pr_diff_reader.py` | snapshot, `cache_identity`, `build_pr_diff`, `aclose` |
| `SessionPRDiffReader` | Protocol (runtime-checkable) | `pr_diff_reader.py` | `open_pr_diff_session` only |
| `InputValidatorProtocol` | Protocol | `input_validation.py` | URL/path/token/sanitize contracts |
| `RequestCoalescingProtocol` | Protocol | `request_coalescing.py` | `coalesce` / clear / stats |
| `RateLimiterProtocol` | Protocol | `protocols.py` | Rate limit checks |
| `MetricsTrackerProtocol` | Protocol | `protocols.py` | Request metrics |
| `GitLabPROperationsProtocol` | Protocol | `protocols.py` | GitLab MR approve (optional keyword-only `expected_head_sha`) + description for MCP tools |
| `HealthMonitorProtocol` | Protocol | `protocols.py` | Health status |
| `ServerConfigurationProtocol` | Protocol | `protocols.py` | Transport/server config |
| `AuthenticationProtocol` | Protocol | `protocols.py` | Auth + client id extraction |

## SESSION PATH (strict full-diff)
- Strict sessions implement `SessionPRDiffReader`: one `open_pr_diff_session` → snapshot + `cache_identity` → cache key/token → `build_pr_diff` → always `aclose` in `finally`.
- Every session exposes `cache_identity: StrictPRDiffCacheIdentity` (provider-neutral key + validation token + schema_version).
- GitHub identity: `github-full-diff-v3:{owner}:{repo}:{pr}:{merge_base}:{head}` + `merge_base:head` token; cached value is the bare `PRDiff`.
- GitLab identity: `gitlab-full-diff-v1:{host}:{ns}:{repo}:{iid}:{ver}:{base}:{start}:{head}` + version/refs token (schema 1); host from request `base_url` (port-aware).
- `open_pr_diff_session(..., *, base_url=None)` on every implementation; use case calls once (GitHub ignores host).
- Snapshot fields: `base_tip_sha`, `merge_base_sha`, `head_sha`, `authoritative_changed_files`.
- The session path is the only diff path; readers expose no `get_pr_diff` / `get_latest_commit_sha` convenience methods.
- MCP composition checks `isinstance(reader, SessionPRDiffReader)` at startup (factory + `StrictDiffCapability`).

## CONVENTIONS
- Prefer `Protocol` / ABC with explicit method signatures.
- Keep application-agnostic except where MCP orchestration needs a stable port (`protocols.py`).
- Session ports return domain entities (`PRDiff`), never SDK models.

## ANTI-PATTERNS
- NO implementations in this package.
- NO re-exports from `__init__.py`; import from the defining module.
- NO FastMCP / framework types.
- NO skipping `aclose` on open sessions.
- NO putting session logic only on infrastructure without this domain contract.
