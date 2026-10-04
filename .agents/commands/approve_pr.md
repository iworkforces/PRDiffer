---
name: approve_pr
description: Approve GitHub PR/GitLab MR with given pr_url
argument-hint: "<pr_url>"
---

# Approve GitHub PR/GitLab MR with given `pr_url`

Review the pull request identified by `$1`. `$1` is the sole argument and is
`pr_url`.

1. Call `prdiffer-mcp_get_pr_diff` with given `pr_url`. The returned payload
   exposes a top-level `files` array (`{"files": [...]}`), not `result.files`.
   If the tool display is truncated and the full output is saved to a file,
   inspect the saved payload before assessing completeness. Display truncation
   alone does not mean the diff is incomplete. If the fetch fails, the payload
   is incomplete, or the top-level `files` array is missing, stop immediately
   without approving the PR/MR.
2. Create one immutable review snapshot from every top-level `files` entry in
   provider order. For each entry, include its `path`, `previous_path` when
   present, `status`, `stats`, and complete full-context `diff`. Do not
   partition, sample, truncate, or omit files, statuses, or file types.
3. Craft a concise and insightful GitHub PR/GitLab MR compliment. Use the `humanizer` skill to refine it then call `prdiffer-mcp_approve_pr` with that compliment.
