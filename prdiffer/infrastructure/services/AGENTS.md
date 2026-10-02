# AGENTS.md - Infrastructure/Services

**Package:** 0.6.2  
Concrete service adapters implementing domain service ports.

## STRUCTURE
```
prdiffer/infrastructure/services/
└── pr_diff_service.py   # GitHubPRDiffService (~194) — session-capable GitHub reader
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **High-level PR diff** | `pr_diff_service.py` | Orchestrates GitHub API + inventory + processor + generator + cache |
| **Session path** | `open_pr_diff_session` | Delegates to `github/pr_diff_session.GitHubSessionPRDiffReader` |
| **Strict assembly** | `_build_pr_diff_strict` | `GeneratedFileDiff` → `FileDiffResponse`; size limits |
| **Inventory** | `_generate_diff_content` | Snapshot-bound `prepare_selected_inventory` then ordered tree/blob processing |

## CONVENTIONS
- Structurally implements `SessionPRDiffReader` (`open_pr_diff_session`); composes `github/` rather than duplicating logic.
- Maps ordered `GeneratedFileDiff` results to `FileDiffResponse` (`path`, `status`, `stats`, `diff`, `previous_path`).
- Enforces per-file and aggregate public-diff character limits via `utils/diff_limits` (hard fail, no truncation).
- No method-level caching; `GetPRDiffUseCase` caches by session snapshot identity.
- Full-diff incompleteness raises `FullDiffIncompleteError` → **E5020**; unexpected generation defects → E5003.
- `diff_generator` / `file_processor` are required keyword args; `GitHubAPIClient` defaults when omitted (factory-wired from `GitHubConfig`).

## ANTI-PATTERNS
- NO MCP/tool concerns here (application layer).
- NO returning partial file lists when inventory/admission/generation fails.
- NO truncating public diffs on the full-diff path.
- NO second full metadata open when a session path already holds repo/PR handles.
