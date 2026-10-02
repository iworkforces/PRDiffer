# AGENTS.md - Domain Layer

Pure business logic. No external deps, no I/O. Entities, ports (ABC/Protocol), use cases, and factory contracts only.

## OVERVIEW
Package **0.6.2**, Python **3.14.4+**.
**34** modules across root + **7** subpackages (no package-level `__init__.py` — import concrete modules). Frozen dataclasses for most entities; ABC/Protocol for ports.

## STRUCTURE
```
prdiffer/domain/
├── entities/                 # PRDiff, FilePatchInfo (~347), FileDiffResponse, typed content, cache identity
├── services/                 # Service interfaces (ABC only)
├── usecases/                 # GetPRDiff (session)
├── repositories/             # PRDiffRepositoryInterface
├── interfaces/               # SessionPRDiffReader, input validation, coalescing, app Protocols
├── config/                   # GitHubConfig + GitLabConfig + GitHubConfigInterface
├── factories/                # ApplicationFactoryInterface, InfrastructureFactoryInterface
├── error_codes.py            # E1xxx–E5xxx constants, incl. E5020 + GitLab E2006/E2007/E3006/E5021 (~418)
├── errors.py                 # ErrorCategory + ErrorCode types (~45)
└── exceptions.py             # PRDifferException hierarchy (~593); FullDiffIncompleteError, GitLabAPIError
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Rich domain model** | `entities/file_patch.py` | ~347 lines; priority, smells, modes, validate, stats |
| **MCP response shape** | `entities/file_diff_response.py`, `entities/pr_diff.py` | Frozen; `previous_path` on renames |
| **Typed content** | `entities/file_content.py` | `FileContentAvailable` / `FileContentUnavailable` |
| **Generated diff unit** | `entities/generated_file_diff.py` | `GeneratedFileDiff` (index, path, previous_path, diff) |
| **Strict cache identity** | `entities/pr_diff_cache.py` | GitHub v3 (merge-base+head) + GitLab v1 (host-aware) |
| **Session PR path** | `interfaces/pr_diff_reader.py` + `usecases/pr_diff_usecases.py` | `SessionPRDiffReader` open → identity → build → close |
| **Service interfaces** | `services/*.py` | ABC + `@abstractmethod` |
| **App component Protocols** | `interfaces/protocols.py` | RateLimiter, Auth, Metrics, Health, `GitLabPROperationsProtocol`, … (~210) |
| **Error codes** | `error_codes.py` + `errors.py` | Structured E-codes |
| **Full-diff incomplete** | `exceptions.py` | `FullDiffIncompleteError` + `FullDiffIncompleteReason` |
| **GitHub config VO** | `config/github_config.py` | Size limits (`max_total_chars` 600k), parallel flags default `true` |
| **GitLab config VO** | `config/gitlab_config.py` | Limits + `allowed_hosts` (default `gitlab.com`) |
| **Factory contracts** | `factories/` | Dependency inversion for outer layers |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `PRDiff` | Entity | `entities/pr_diff.py` | `files: tuple[FileDiffResponse, ...]` |
| `FileDiffResponse` | Entity | `entities/file_diff_response.py` | path/status/stats/diff/`previous_path` |
| `FilePatchInfo` | Entity | `entities/file_patch.py` | Rich review model (~347) |
| `FileContentAvailable` / `Unavailable` | Entity | `entities/file_content.py` | Typed content acquisition |
| `GeneratedFileDiff` | Entity | `entities/generated_file_diff.py` | One generated full-context file |
| `StrictPRDiffCacheIdentity` | Entity | `entities/pr_diff_cache.py` | Provider-neutral cache key + token |
| `SessionPRDiffReader` | Protocol | `interfaces/pr_diff_reader.py` | `open_pr_diff_session` |
| `GetPRDiffUseCase` | Use case | `usecases/pr_diff_usecases.py` | Session path (+ `base_url`) (~60) |
| `E5020_FULL_DIFF_INCOMPLETE` | ErrorCode | `error_codes.py` | Full-diff incompleteness |
| `E2006_GITLAB_AUTH_FAILED` | ErrorCode | `error_codes.py` | GitLab 401 |
| `E2007_GITLAB_INSUFFICIENT_PERMISSIONS` | ErrorCode | `error_codes.py` | GitLab 403 |
| `E3006_GITLAB_RATE_LIMITED` | ErrorCode | `error_codes.py` | GitLab 429 |
| `E5021_GITLAB_API_ERROR` | ErrorCode | `error_codes.py` | GitLab 5xx / upstream |
| `GitLabAPIError` | Exception | `exceptions.py` | Operational GitLab errors; optional status_code |
| `FullDiffIncompleteError` | Exception | `exceptions.py` | Maps to E5020; safe details only |
| `GitHubConfig` | Config VO | `config/github_config.py` | Frozen; full-diff admission limits |
| `GitLabConfig` | Config VO | `config/gitlab_config.py` | Frozen+slots; limits + host allowlist |

## CONVENTIONS

### Purity
- **NO imports from infrastructure/application**.
- No network, filesystem, Dynaconf, PyGithub, or logging backends.
- Enforce with `python3 scripts/analyze_dependencies.py --path prdiffer`.

### Entities
- Prefer `@dataclass(frozen=True)` (`PRDiff`, `FileDiffResponse`, content/cache types).
- Use `tuple[T, ...]` for sequences (hashable).
- **Rich**: `FilePatchInfo` — review priority, code smells, optional modes, validation.
- **Anemic/DTO**: `PRDiff`, `FileDiffResponse` hold structured response data.
- Note: `PullRequest` / `Repository` use non-frozen `@dataclass` (mutable value objects).

### Interfaces
- Domain defines ports; infrastructure implements adapters.
- Application component Protocols live in `interfaces/protocols.py` so factories depend on domain contracts.
- Both GitHub and GitLab full-diff use session ports (`open_pr_diff_session`); GitLab also accepts optional `base_url`.
- Structural session detection inspects the **concrete type** so MagicMock doubles do not take the session path accidentally.

### Error Model
- Exception hierarchy in `exceptions.py` (auth, rate limit, validation, not found, cache, config, processing, security, …).
- Structured codes: `ErrorCode` / `ErrorCategory` types in `errors.py`, constants in `error_codes.py`; exceptions carry an `error_code`.
- **Strict full-diff incompleteness**: `E5020_FULL_DIFF_INCOMPLETE` + `FullDiffIncompleteError(GitHubAPIError)` with `FullDiffIncompleteReason`:
  - `INVENTORY_TRUNCATED`, `FILE_COUNT_LIMIT`, `BINARY_CONTENT`, `FILE_SIZE_LIMIT`, `CONTENT_UNAVAILABLE`, `CONTENT_DECODE_FAILED`, `UNSUPPORTED_FILE_STATUS`, `DIFF_GENERATION_FAILED`, `RESPONSE_SIZE_LIMIT`
  - Safe details only: `reason`, `path`, `previous_path`, `observed`, `limit` — never tokens or raw content.
- Do **not** remap auth/permission/rate-limit/retry-exhausted network failures to E5020; unexpected algorithm defects stay `E5003_DIFF_GENERATION_ERROR`.
- GitLab operational mapping: 401→E2006, 403→E2007, 429→E3006, 5xx→E5021; reuse E4001/E4002/E4003 for verified project/MR/file 404, E5004 timeout, E5019 connection. Never put `response_body`/tokens/credentials in details.

### Full-diff correctness (0.6.x)
- Success responses are complete by construction (no completeness boolean).
- `FileDiffResponse.previous_path` only for `EDIT_TYPE.RENAMED`.
- Content union: available empty text ≠ deterministic unavailability; operational failures raise.
- Aggregate response budget: `max_total_chars` default **600_000** (E5020/`RESPONSE_SIZE_LIMIT` on overflow).
- Cache: `github-full-diff-v3` (merge-base+head) and host-aware `gitlab-full-diff-v1:{host}:…` keys hold bare `PRDiff` values; `unwrap_pr_diff_cache_value` requires the exact session identity key.
- Sessions expose `StrictPRDiffCacheIdentity` (provider-neutral key + validation token). GitHub snapshot: `base_tip_sha` + `merge_base_sha` + `head_sha` + authoritative count; post-build drift → E5020 `SNAPSHOT_CHANGED`.

## ANTI-PATTERNS
- **NO outer-layer imports** in domain.
- **NO I/O** in entities or use cases (inject ports; adapters live outside).
- **NO concrete infrastructure types** as domain dependencies.
- **NO mutable shared entities** where frozen is expected → prefer `frozen=True` + `tuple`.
- **NO `list` fields on frozen dataclasses** → use `tuple`.
- **NO Pydantic in domain** → frozen dataclasses.
- **NO leaking raw content/tokens** in `FullDiffIncompleteError.details`.
- **NO open-host defaults** in `GitLabConfig.allowed_hosts` (must stay explicit allowlist).

## NOTES
- Domain has no package `__init__` re-exports; import concrete modules (E-codes from `error_codes.py`, not `errors.py`).
- Single error surface: `exceptions.PRDifferException` hierarchy with `ErrorCode` values; tools map them to MCP `ToolError` payloads.
- Internal graph: `usecases/pr_diff_usecases.py` depends on `interfaces/pr_diff_reader.SessionPRDiffReader` (ports do not import use cases).
- MCP tools wire `GetPRDiffUseCase` for diffs; approve/describe use cases are available for tests but not the primary MCP path.
