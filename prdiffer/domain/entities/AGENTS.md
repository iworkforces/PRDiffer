# AGENTS.md - Domain/Entities

Frozen domain models for PR diffs, content results, and cache identity. Package 0.6.2.

## STRUCTURE
```
prdiffer/domain/entities/
├── file_patch.py            # FilePatchInfo + EDIT_TYPE — rich model (+ optional modes) (~347)
├── file_diff_response.py    # FileDiffResponse, FileStats (~54)
├── file_content.py          # Typed content union (~41)
├── generated_file_diff.py   # GeneratedFileDiff (~19)
├── pr_diff_cache.py         # StrictPRDiffCacheIdentity + GitHub v3 / GitLab v1 keys
├── pr_diff.py               # PRDiff — files tuple of FileDiffResponse (~17)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Business methods** | `file_patch.py` | `calculate_review_priority`, `detect_code_smells`, `validate` |
| **MCP file payload** | `file_diff_response.py` | path, status, stats, diff, `previous_path` (renames only) |
| **Typed content** | `file_content.py` | Available empty text vs deterministic unavailability |
| **Generated unit** | `generated_file_diff.py` | index + path + previous_path + full-context `diff` |
| **Strict cache identity** | `pr_diff_cache.py` | `StrictPRDiffCacheIdentity`; GitHub v3 / GitLab v1 builders |
| **Aggregate response** | `pr_diff.py` | `files: tuple[FileDiffResponse, ...]` |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `EDIT_TYPE` | StrEnum | `file_patch.py` | added/deleted/modified/renamed/unknown |
| `FilePatchInfo` | Frozen dataclass | `file_patch.py` | Rich file change model; optional `old_mode`/`new_mode` (six-digit octal) |
| `FileStats` | Frozen dataclass | `file_diff_response.py` | additions/deletions |
| `FileDiffResponse` | Frozen dataclass | `file_diff_response.py` | Public MCP file payload |
| `FileContentAvailable` | Frozen dataclass | `file_content.py` | Successful text (incl. empty) |
| `FileContentUnavailable` | Frozen dataclass | `file_content.py` | Deterministic unavailability |
| `FileContentUnavailableReason` | StrEnum | `file_content.py` | BINARY, SIZE, DIRECTORY, NOT_FOUND, DECODE |
| `FileContentResult` | Alias | `file_content.py` | Available \| Unavailable |
| `GeneratedFileDiff` | Frozen dataclass | `generated_file_diff.py` | One generated full-context file |
| `PRDiff` | Frozen dataclass | `pr_diff.py` | Aggregate files tuple |
| `StrictPRDiffCacheIdentity` | Frozen dataclass | `pr_diff_cache.py` | cache_key + validation_token + schema_version |
| `github_full_diff_v3_key` | Function | `pr_diff_cache.py` | Active GitHub key: `…-v3:{owner}:{repo}:{pr}:{merge_base}:{head}` |
| `github_full_diff_v3_identity` | Function | `pr_diff_cache.py` | Active identity (token `merge_base:head`) |
| `gitlab_full_diff_v1_key` | Function | `pr_diff_cache.py` | `gitlab-full-diff-v1:{host}:{ns}:{repo}:{iid}:{ver}:{base}:{start}:{head}` |
| `gitlab_full_diff_v1_identity` | Function | `pr_diff_cache.py` | GitLab identity (host-aware key + version/refs token) |

## CONVENTIONS
- Prefer `@dataclass(frozen=True)` for diff/content/cache models.
- Rich logic stays on `FilePatchInfo`; response DTOs stay thin.
- Map infrastructure patches → `FileDiffResponse` at the adapter boundary.
- `FileDiffResponse.previous_path` is optional and valid **only** for `EDIT_TYPE.RENAMED` (`__post_init__` invariant; must differ from `path`). Success responses remain complete by construction — no completeness boolean.
- GitLab maps `old_path` → `previous_path` on renames only; otherwise `None`.
- Content: operational failures (auth, rate limit, transport) **raise**; do not fold into `FileContentUnavailable`.
- Cache helper: `unwrap_pr_diff_cache_value` accepts only a bare `PRDiff` stored under a strict GitHub-v3 / GitLab-v1 key (exact `identity.cache_key` when an identity is given).
- Sessions expose `StrictPRDiffCacheIdentity` (provider-neutral); GitHub v3 keys bind merge-base+head; GitLab keys include **host** (port-aware for non-80/443).

## ANTI-PATTERNS
- NO I/O or framework types.
- NO Pydantic `BaseModel` in this package.
- NO mutating frozen fields after construction.
- NO inventing alternate GitHub key prefixes; only `github-full-diff-v3` is valid.
- NO treating binary/size/decode limits as soft partial success in the public response.
