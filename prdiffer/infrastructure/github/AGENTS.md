# AGENTS.md - Infrastructure/GitHub

**Package:** 0.6.2  
PyGithub-backed API client, inventory admission, ordered file processing, full-context diff generation, and request sessions. Critical path for full-diff correctness.

## STRUCTURE
```
prdiffer/infrastructure/github/
├── client.py                # GitHubAPIClient: client init + retry/CB-wrapped repo/PR lookup (~174)
├── client_models.py         # GITHUB_API_EXCEPTIONS (~13)
├── file_processor.py        # Ordered selected files → FilePatchInfo from git trees/blobs (~285)
├── diff_generator.py        # generate_ordered_file_diffs → GeneratedFileDiff (~198)
├── inventory.py             # Authoritative inventory + admission (~115)
├── git_objects.py           # Immutable tree/blob descriptors + load/resolve helpers (~431)
├── pr_diff_session.py       # anyio session + merge-base capture + v3 cache_identity + revalidate (~368)
└── __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **API client** | `client.py` | Implements `GitHubAPIServiceInterface`; retry/CB-wrapped `_get_pygithub_repository` / `_get_pygithub_pull_request` |
| **Inventory admission** | `inventory.py` | Authoritative `changed_files` vs enumeration; selected N+1 → E5020 |
| **Immutable trees/objects** | `git_objects.py` | Recursive tree load; blob/symlink/gitlink resolve; rename previous check |
| **Ordered file processing** | `file_processor.py` | Merge-base/head recursive trees + blobs; missing `get_git_tree` → E5020 |
| **Full-context diffs** | `diff_generator.py` | `GeneratedFileDiff` in provider order; hard-fail incompleteness |
| **Request session** | `pr_diff_session.py` | anyio `to_thread` + CapacityLimiter; one metadata lookup per request |

## CONVENTIONS

### File content (git trees/blobs)
- Content comes from immutable recursive trees at the merge-base and head SHAs plus blobs by object id (`git_objects.py`).
- `resolve_entry_text` returns `FileContentAvailable | FileContentUnavailable`; empty text is **available** (`text == ""`).
- Binary / oversized / undecodable / missing entries → E5020; auth / rate-limit / transport failures raise operational exceptions.

### Inventory (strict)
- Fully materialize provider file pages, then validate authoritative `changed_files` vs enumerated count.
- Authoritative count > 3000 → inventory truncated (E5020 path).
- Ignore/extension filter, then **selected-file admission**: exactly N succeeds; N+1 → `FILE_COUNT_LIMIT` (E5020).

### Ordered processing + generation
- `FileProcessor` assembles ordered `FilePatchInfo` (including deleted / rename-only). Renames require distinct `previous_filename` before content.
- Always uses immutable merge-base/head trees: modes on patches, symlinks, gitlinks; no Contents API path.
- `DiffGenerator.generate_ordered_file_diffs` returns one full-context `GeneratedFileDiff` per selected file in order, or hard-fails.
- Mode headers (before rename, then body): `new file mode` / `deleted file mode` / `old mode`+`new mode` for 100644/100755/120000/160000.
- `DiffUtils` emits Git-style `\ No newline at end of file` when either side lacks a trailing newline.
- Never fall back to provider hunk text when reconstruction fails (E5003 / E5020 only).
- Contract inability → **E5020** / `FullDiffIncompleteError`; unexpected defects → E5003.

### Sessions
- `GitHubPRDiffSession` / `GitHubSessionPRDiffReader`: request-local client/repo/PR handles.
- Blocking PyGithub work: acquire session `CapacityLimiter` first, re-check request deadline after the queue wait, then `run_sync(..., abandon_on_cancel=False)` (capacity 1 when parallel fetch disabled).
- A repository without `get_git_tree` (or a truncated tree) is E5020 `INVENTORY_TRUNCATED` — there is no Contents-API fallback.
- One metadata lookup per request; always close/drop strong refs in `aclose`.
- Open captures base tip + head + authoritative count, resolves **merge-base once** via Compare (no base-tip fallback), then returns the session.
- `cache_identity` returns GitHub v3 key + `merge_base:head` token (`github_full_diff_v3_identity`); base-tip-only churn does not change identity.
- `build_pr_diff` passes the immutable snapshot into generation and **revalidates** head/merge-base/count afterward; drift → E5020 `SNAPSHOT_CHANGED` (no cache write).

### Boundary
- Never return raw PyGithub objects past this package boundary.
- Respect rate limits (403/429) via retry utilities.
- Honor ignore patterns / extension allowlists from settings/`GitHubConfig`.

## ANTI-PATTERNS
- NO domain imports of `github.*` SDK.
- NO unbounded file downloads without size limits.
- NO caching unavailable sentinels or empty-string error stand-ins.
- NO completing full-diff with partial file sets when inventory/admission fails.
- NO second metadata lookup inside an open session when handles already exist.
- NO reintroducing a Contents-API / ETag content path alongside the tree path.
