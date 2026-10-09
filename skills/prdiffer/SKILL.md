---
name: prdiffer
description: "Use when the user asks to get a PR/MR diff, analyze or review a pull/merge request, approve a PR/MR, update a PR/MR description, or work with GitHub PRs or GitLab MRs via the PRDiffer server. Triggers include: get pr diff, fetch diff, pr changes, merge request, gitlab mr, approve pr, approve mr, describe pr, update description, what files changed."
---

# PRDiffer — GitHub PR & GitLab MR tools via MCP

PRDiffer exposes MCP tools for:

1. **Strict full-context** PR/MR diffs (all-or-nothing)
2. **Approving** a PR/MR with a non-empty compliment
3. **Updating** a PR/MR description/body

Tools are prefixed with `prdiffer__` in the MCP namespace. All three VCS tools accept **GitHub pull request URLs** and **GitLab merge request URLs** (including nested groups and allowlisted custom hosts). A fourth tool, `prdiffer__health`, is provider-agnostic.

## Prerequisites

A running PRDiffer server must be configured in the agent's MCP settings:

```json
{
  "mcp": {
    "prdiffer": {
      "type": "remote",
      "url": "http://127.0.0.1:9102/mcp",
      "enabled": true
    }
  }
}
```

**Server-side provider tokens** (at least one required for VCS tools):

| Env | Purpose |
|-----|---------|
| `GITHUB_TOKEN` | GitHub API (diff, approve review, edit body) |
| `GITLAB_TOKEN` | GitLab API — **read** (`read_api` + `read_repository`) for `get_pr_diff`; **write** (`api` or equivalent) for `approve_pr` / `describe_pr` |
| `GITLAB_ALLOWED_HOSTS` | CSV bare hostnames (default `gitlab.com`). Required for custom/self-hosted GitLab |
| `MAX_FILES_ALLOWED` | Selected-file admission cap (default 50) |

If the server has `MCP_AUTH_ENABLED=true`, pass a valid `api_key` on every tool call.

---

## Supported URL formats

| Provider | Pattern | Example |
|----------|---------|---------|
| **GitHub** | `https://github.com/{owner}/{repo}/pull/{number}` | `https://github.com/acme/app/pull/42` |
| **GitLab.com** | `https://gitlab.com/{namespace}/{project}/-/merge_requests/{iid}` | `https://gitlab.com/acme/app/-/merge_requests/17` |
| **GitLab nested** | Namespace may include subgroups | `https://gitlab.com/group/sub/project/-/merge_requests/3` |
| **Custom GitLab** | Same path shape; host must be allowlisted | `https://gitlab.example.com/team/svc/-/merge_requests/9` |

Notes:

- Use HTTPS only. No query strings, fragments, or credentials in the URL.
- Nested GitLab: `repo_owner` is the full namespace (`group/sub`), `repo_name` is the project.
- Disallowed GitLab hosts → `E1001_INVALID_URL` (SSRF protection with the server token).

---

## Tools

### prdiffer__get_pr_diff

Fetch a **complete structured full-context** PR/MR diff (primary analysis tool).

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pr_url` | `string` | Yes | GitHub PR or GitLab MR URL (see formats above) |
| `api_key` | `string` | No | MCP API key when server auth is enabled |

**Returns:** `PRDiff`:

```
PRDiff {
  files: FileDiffResponse[]   // Changed files in provider order
  head_sha: string            // Snapshot head, including cache hits and empty diffs
}
```

Each `FileDiffResponse`:

```
FileDiffResponse {
  path: string                // e.g. "src/main.py"
  status: EDIT_TYPE           // "added" | "deleted" | "modified" | "renamed"
  previous_path: string|null  // Prior path for renames only
  stats: FileStats {
    additions: int
    deletions: int
  }
  diff: string                // Generated full-context unified diff (not hunk-only)
}
```

**Strict completeness:** Success means every selected file is fully reconstructed. Partial/truncated `files` lists are never returned. Failures use `E5020_FULL_DIFF_INCOMPLETE` with a stable `reason`:

`INVENTORY_TRUNCATED` · `FILE_COUNT_LIMIT` · `BINARY_CONTENT` · `FILE_SIZE_LIMIT` · `CONTENT_UNAVAILABLE` · `CONTENT_DECODE_FAILED` · `UNSUPPORTED_FILE_STATUS` · `DIFF_GENERATION_FAILED` · `RESPONSE_SIZE_LIMIT` · `SNAPSHOT_CHANGED`

At the MCP boundary, E5020 is often a `ToolError` with compact JSON: `{"error_code","message","details"}` (safe details only; no `files` payload).

**Provider behavior:**

| | GitHub | GitLab |
|--|--------|--------|
| Snapshot | Immutable merge-base + head + file count (Compare once; revalidate after build) | Immutable MR diff version pinned to `diff_refs` |
| Diff text | Generated full-context unified (modes/EOF; symlink/gitlink canonical text) | Same (not raw hunk-only provider patch) |
| Cache | `github-full-diff-v3:{owner}:{repo}:{pr}:{merge_base}:{head}`  | `gitlab-full-diff-v1:{host}:…` (port-aware host) |

**Examples:**

```
// GitHub
result = prdiffer__get_pr_diff(
  pr_url="https://github.com/owner/repo/pull/42"
)

// GitLab.com
result = prdiffer__get_pr_diff(
  pr_url="https://gitlab.com/group/project/-/merge_requests/17"
)

// Nested group + custom host (host must be in GITLAB_ALLOWED_HOSTS)
result = prdiffer__get_pr_diff(
  pr_url="https://gitlab.example.com/group/sub/project/-/merge_requests/3"
)

for file in result.files:
  print(file.path, file.status, file.stats.additions, file.stats.deletions)
  print(file.diff)
```

---

### prdiffer__approve_pr

Approve a GitHub PR or GitLab MR and attach a **non-empty** compliment.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pr_url` | `string` | Yes | GitHub PR or GitLab MR URL |
| `compliment` | `string` | Yes | Non-empty compliment text (review body / note) |
| `api_key` | `string` | No | MCP API key when server auth is enabled |
| `expected_head_sha` | `string|null` | No | 40/64 hexadecimal characters from get_pr_diff's `head_sha`; uppercase is accepted |

**Returns:** `string` success message.

**Provider behavior:**

| Provider | What the server does |
|----------|----------------------|
| **GitHub** | Creates a PR review with `event=APPROVE` and `body=compliment`; when a SHA is supplied, checks the current head and binds the review to that commit |
| **GitLab** | Creates a **note** with the compliment, then calls MR **approve** (two steps; note-first so a note failure cannot leave the MR approved while the tool errors) |

Supplying `expected_head_sha` refuses approval with `E1011_HEAD_SHA_MISMATCH` if the head moved. Omitted/null retains existing behavior.
GitLab checks the head and sends the SHA with approval. If approval is definitively rejected after creating the note, the server removes that
compliment note; `details.compliment_note` reports `deleted` or `cleanup_failed` (only the note created by this request is cleaned up).

**Requirements:**

- `compliment` must be non-empty (empty string is rejected before the provider call).
- Server token needs write permission on the target (GitHub: review rights; GitLab: `api` / approve + notes).

**Examples:**

```
// GitHub
prdiffer__approve_pr(
  pr_url="https://github.com/owner/repo/pull/42",
  compliment="Clean refactor and solid test coverage — LGTM."
)

// GitLab
prdiffer__approve_pr(
  pr_url="https://gitlab.com/group/project/-/merge_requests/17",
  compliment="Nice breakdown of the migration steps."
)
```

**Agent tips:** Always fetch the diff first (`get_pr_diff`), ground the compliment in real changes, and avoid empty or generic praise.

---

### prdiffer__describe_pr

Replace the PR/MR description (body) with **non-empty** text.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pr_url` | `string` | Yes | GitHub PR or GitLab MR URL |
| `pr_description` | `string` | Yes | New non-empty description (Markdown OK) |
| `api_key` | `string` | No | MCP API key when server auth is enabled |

**Returns:** `string` success message.

**Provider behavior:**

| Provider | Field updated |
|----------|----------------|
| **GitHub** | PR `body` |
| **GitLab** | MR `description` |

**Examples:**

```
// GitHub
prdiffer__describe_pr(
  pr_url="https://github.com/owner/repo/pull/42",
  pr_description="## Summary\nAdds OAuth2 login.\n\n## Test plan\n- [ ] Login happy path"
)

// GitLab
prdiffer__describe_pr(
  pr_url="https://gitlab.com/group/project/-/merge_requests/17",
  pr_description="## Why\nUnblocks release train.\n\n## Changes\n- Extract shared retry helper"
)
```

**Agent tips:** Prefer structured Markdown (Summary / Changes / Test plan). Derive content from `get_pr_diff` so the description matches the actual files.

---

### prdiffer__health

Provider-agnostic server health / metrics snapshot (no PR URL). Use for connectivity checks; not required for normal review workflows.

---

## Common workflows

### 1. Analyze PR/MR changes

1. Obtain a full GitHub PR or GitLab MR URL (do not invent hosts).
2. Call `prdiffer__get_pr_diff(pr_url)`.
3. Iterate `result.files`: status, stats, full-context `diff`.
4. On `E5020`, read `reason` and explain that the server refuses partial diffs.

### 2. Review and approve

1. `result = prdiffer__get_pr_diff(pr_url)`; retain `result.head_sha`.
2. Review statuses, patches, and risk (secrets, large binary, incomplete tests).
3. If criteria pass: `prdiffer__approve_pr(pr_url, compliment, expected_head_sha=result.head_sha)` with a **specific** compliment.
4. Same URL works for GitHub and GitLab; do not convert MR URLs to fake GitHub URLs.
5. On E1011, re-run get_pr_diff and re-review before approving the new head.

### 3. Summarize and update description

1. `prdiffer__get_pr_diff(pr_url)`.
2. Write Markdown from file list + stats + diff themes.
3. `prdiffer__describe_pr(pr_url, pr_description)`.

### 4. Aggregate statistics

```
result = prdiffer__get_pr_diff(pr_url)
n_files = len(result.files)
additions = sum(f.stats.additions for f in result.files)
deletions = sum(f.stats.deletions for f in result.files)
by_status = {}  // group file.path by file.status
```

---

## Errors

Handle by structured `error_code` when present:

| Error code | Meaning | Recovery |
|------------|---------|----------|
| `E1001_INVALID_URL` | Bad/unsupported URL, disallowed GitLab host, empty required text, malformed expected SHA | Fix URL/text; use the 40/64-hex `head_sha` from get_pr_diff |
| `E1011_HEAD_SHA_MISMATCH` | Approval refused because the head moved; details: `expected_head_sha`, optional `actual_head_sha`, optional `compliment_note` (`deleted`/`cleanup_failed`) | Re-run get_pr_diff and re-review the new head; check note cleanup status on GitLab |
| `E2002_AUTH_FAILED` | Missing/invalid MCP `api_key` | Pass valid `api_key` or disable client-side auth assumption |
| `E2006_GITLAB_AUTH_FAILED` | GitLab token rejected (401) | Fix `GITLAB_TOKEN` |
| `E2007_GITLAB_INSUFFICIENT_PERMISSIONS` | GitLab 403 | Broader token scopes / project membership |
| `E3001_RATE_LIMITED` | MCP server rate limit | Back off and retry |
| `E3006_GITLAB_RATE_LIMITED` | GitLab API 429 | Back off; honor `retry_after` if exposed |
| `E4001_REPO_NOT_FOUND` | Project/repo not found | Check path and token access |
| `E4002_PR_NOT_FOUND` | PR/MR not found | Check number/iid |
| `E5002_GITHUB_API_ERROR` | GitHub API / generic provider failure | Check `GITHUB_TOKEN`, repo, PR |
| `E5004_TIMEOUT_ERROR` | Provider timeout | Retry; large PRs may need higher server timeouts |
| `E5019_CONNECTION_ERROR` | Network to provider failed | Connectivity / host |
| `E5020_FULL_DIFF_INCOMPLETE` | Strict full-diff failed | See `reason`; reduce size; no partial files |
| `E5021_GITLAB_API_ERROR` | GitLab 5xx / generic API error | Retry; check GitLab status |

```
try:
  result = prdiffer__get_pr_diff(pr_url)
except Exception as e:
  code = getattr(e, "error_code", None) or str(e)
  // Branch on E1001 / E2002 / E2006 / E3001 / E3006 / E5020 / E5021 / E5002 …
```

For E5020 via MCP `ToolError`, parse JSON text for `error_code`, `message`, and `details.reason`.
E1011 also uses compact JSON `ToolError` text with exactly `error_code`, `message`, and `details`; unavailable actual heads are omitted.

---

## Constraints

- **URL**: Full HTTPS GitHub PR **or** GitLab MR URL only (formats above). Never pass owner/repo alone without the path shape.
- **GitLab hosts**: Only allowlisted hosts (`gitlab.com` by default; opt-in via `GITLAB_ALLOWED_HOSTS`).
- **Empty bodies**: `compliment` and `pr_description` must be non-empty strings.
- **Auth**: With `MCP_AUTH_ENABLED=true`, every tool needs a valid `api_key`.
- **Rate limits**: Avoid tight loops on `get_pr_diff`.
- **Caching**: Diff responses are cached by provider identity (GitHub merge-base+head v3 / GitLab version+refs). Re-fetch after new pushes.
- **Filtering**: Server-side ignore/extension policy may exclude files before admission; selected set is still all-or-nothing.
- **Strict diffs**: Never invent a partial file list when the tool errors with E5020.
- **Do not** rewrite a GitLab MR URL into a GitHub URL (or vice versa); route by the real host/path.

---

## GitLab notes (all VCS tools)

- **Nested namespaces**: `https://gitlab.com/a/b/c/-/merge_requests/1` → owner `a/b`, project `c`, iid `1`.
- **Custom hosts**: MR URL host must appear in server `GITLAB_ALLOWED_HOSTS` (bare hostname).
- **Approve**: approve API + note body (not identical to GitHub’s single review call).
- **Describe**: updates MR `description`.
- **Diff failures**: E5020 fail-closed; equal-content equal-mode modified is fail-closed; no partial `files`.
- **Operational codes**: E2006, E2007, E3006, E4001–E4003, E5004, E5019, E5021.

---

## Quick decision guide

| User intent | Tool | `pr_url` source |
|-------------|------|-----------------|
| What changed? | `get_pr_diff` | GitHub PR or GitLab MR |
| Approve after review | `approve_pr` | Same URL as the diff |
| Rewrite description | `describe_pr` | Same URL as the diff |
| Is the server up? | `health` | — |
