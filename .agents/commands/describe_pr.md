---
name: describe_pr
description: Describe GitHub PR/GitLab MR with given pr_url
argument-hint: "<pr_url>"
---

# Describe GitHub PR/GitLab MR with given `pr_url`

Review the pull request identified by `$1`. `$1` is the sole argument and is
`pr_url`.

1. Call `prdiffer-mcp_get_pr_diff` with given `pr_url`. The returned payload
   exposes a top-level `files` array (`{"files": [...]}`), not `result.files`.
   If the tool display is truncated and the full output is saved to a file,
   inspect the saved payload before assessing completeness. Display truncation
   alone does not mean the diff is incomplete. If the fetch fails, the payload
   is incomplete, or the top-level `files` array is missing, stop immediately
   without creating or changing the PR/MR description.
2. Based on what files changed, craft a concise and insightful PR/MR description. Use the `humanizer` skill to refine it then call `prdiffer-mcp_describe_pr` with that description.
