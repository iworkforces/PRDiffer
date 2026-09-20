# PRDiffer

[![Python Version](https://img.shields.io/badge/python-3.14.4%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PR Quality](https://github.com/iWorkforces/PRDiffer/actions/workflows/pr-quality.yml/badge.svg)](https://github.com/iWorkforces/PRDiffer/actions/workflows/pr-quality.yml)

A Model Context Protocol (MCP) server that provides comprehensive GitHub Pull Request diff analysis with full file context for AI assistants and code review tools.

## Overview

PRDiffer is an MCP server that extracts and analyzes GitHub PR diffs, providing AI assistants with rich context for code review, analysis, and assistance. Unlike standard diff tools that only show changed hunks, PRDiffer provides full-file context, commit messages, and intelligent file filtering.

### Key Features

- **Full-File Context Diffs**: See entire files with changes, not just hunks
- **Commit Message Integration**: Access PR commit history and messages
- **Intelligent File Filtering**: Pattern-based filtering to focus on relevant files
- **Smart Caching**: Commit-based cache invalidation ensures fresh data with optimal performance
- **Security First**: Comprehensive input validation, rate limiting, and authentication support
- **Multiple Transport Modes**: Support for stdio, HTTP, SSE, and streamable-HTTP
- **Async Architecture**: Built with anyio for efficient concurrent processing
- **Clean Architecture**: Domain-driven design with clear separation of concerns

## Quick Start

### Prerequisites

- Python 3.14.4 or higher
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (required by the launcher)
- GitHub or GitLab Personal Access Token (for authenticated requests)

### Installation

```bash
# Clone the repository
git clone https://github.com/iWorkforces/PRDiffer.git
cd PRDiffer

# Provision the locked project environment
uv sync --frozen
```

The launcher uses the already-provisioned project without changing it. It does
not install `uv`, download Python, update `uv.lock`, or sync dependencies. Run
`uv sync --frozen` again after dependency or lockfile changes before restarting
the launcher.

### Basic Usage

1. Set your GitHub token:

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

2. Start the MCP server:

```bash
./start-prdiffer-mcp-server.sh
```

### Accessing the MCP Server

#### Add to Claude Code

```bash
claude mcp add --transport http prdiffer http://127.0.0.1:9102/mcp
```

#### Add to OpenCode

File `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "prdiffer": {
      "type": "remote",
      "url": "http://127.0.0.1:9102/mcp",
      "enabled": true
    }
  }
}
```

#### Add to Grok build

```bash
grok mcp add --transport http prdiffer http://127.0.0.1:9102/mcp
```

## AI Agent Skill

PRDiffer ships with an **[Agent Skill](https://skills.sh)** that teaches AI coding assistants how to use `prdiffer__get_pr_diff`, `prdiffer__approve_pr`, and `prdiffer__describe_pr` MCP tools effectively. The skill provides tool signatures, structured return type documentation, common workflows, error handling patterns, and constraints — so your AI agent can analyze PRs without guessing.

### Install the Skill

```bash
npx skills add iWorkforces/PRDiffer
```

This installs the skill to all detected AI coding agents. For specific agents:

```bash
# Claude Code only
npx skills add iWorkforces/PRDiffer -a claude-code

# OpenCode only
npx skills add iWorkforces/PRDiffer -a opencode

# Multiple agents
npx skills add iWorkforces/PRDiffer -a claude-code -a opencode -a cursor
```

### Supported Agents

The skill is compatible with **50+ AI coding agents** including:

| Agent | Install Flag | Skill Path |
|-------|-------------|------------|
| **Claude Code** | `claude-code` | `.claude/skills/` |
| **OpenCode** | `opencode` | `.agents/skills/` |
| **Cursor** | `cursor` | `.agents/skills/` |
| **Windsurf** | `windsurf` | `.windsurf/skills/` |
| **GitHub Copilot** | `github-copilot` | `.agents/skills/` |
| **Cline** | `cline` | `.agents/skills/` |
| **Gemini CLI** | `gemini-cli` | `.agents/skills/` |
| **Codex** | `codex` | `.agents/skills/` |

For a complete list, see [skills.sh](https://skills.sh).

### What the Skill Teaches Your Agent

After installation, your AI agent will know:

- **When to use each tool** — trigger phrases like "analyze this PR" or "approve this PR" activate the skill
- **Tool signatures** — exact parameter names, types, and whether they're required
- **Return types** — the structured `PRDiff` → `FileDiffResponse` → `FileStats` shape with field-level documentation
- **Error handling** — error codes (`E1001`, `E2002`, `E3001`, `E5002`) and recovery strategies
- **Common workflows** — analyze changes, review & approve, summarize & describe, extract statistics
- **Constraints** — URL format requirements, authentication, rate limiting, caching behavior

### How It Works

The skill's `SKILL.md` (located at `skills/prdiffer/SKILL.md`) activates automatically when your AI agent encounters trigger phrases like "get PR diff", "analyze pull request", or "approve this PR". The agent then uses the documented MCP tool signatures to call the PRDiffer server without guesswork.

The MCP server must still be running separately (`./start-prdiffer-mcp-server.sh`) — the skill only teaches the agent *how* to call the tools, not run them.

### Learn More

- **[skills.sh](https://skills.sh)** — Discover more agent skills
- **[skills/prdiffer/SKILL.md](skills/prdiffer/SKILL.md)** — Read the full skill definition
- **[Agent Skills Specification](https://agentskills.io)** — How skills work across agents

## Using the Skill with AI Coding Agents

Once the `prdiffer` skill is installed and the MCP server is running, you can prompt your AI coding agent naturally. The skill activates automatically and drives the right MCP tool calls. Below are ready-to-use prompts — one per tool.

---

### `prdiffer__get_pr_diff` — Fetch & Analyze a PR

> **Strict full-context diffs:** Successful responses include every selected file with
> generated full-context `diff` text and optional `previous_path` on renames. GitHub keys
> use merge-base + head (`github-full-diff-v3`); snapshot drift returns `SNAPSHOT_CHANGED`.
> Incomplete inventories, oversize/binary content, or generation failures return
> `E5020_FULL_DIFF_INCOMPLETE` with a stable `reason` — never a partial `files` list.


Use this to retrieve the full structured diff and perform a thorough code review.

> **Prompt your agent:**
>
> Use the `prdiffer` skill to get the full diff from `https://github.com/<owner>/<repository>/pull/<pull_number>`, then analyze it thoroughly across multiple aspects — architecture, correctness, security, test coverage, and style — and produce a comprehensive PR review with specific inline observations and actionable recommendations.

The agent will call `prdiffer__get_pr_diff`, iterate over every `FileDiffResponse` in the result, and synthesize a structured review covering changed files, addition/deletion statistics, and full patch content.

---

### `prdiffer__approve_pr` — Review and Approve a PR

Use this after analyzing a PR to submit a formal approval with a meaningful compliment.

> **Prompt your agent:**
>
> Use the `prdiffer` skill to fetch the diff for `https://github.com/<owner>/<repository>/pull/<pull_number>`, verify the changes look correct and safe, then approve the PR with a genuine, specific compliment that references what was done well.

The agent will call `prdiffer__get_pr_diff` to inspect the changes, then invoke `prdiffer__approve_pr` with a compliment derived from the actual diff content (e.g., praising a clean refactor or solid test coverage).

---

### `prdiffer__describe_pr` — Auto-Generate a PR Description

Use this to write or update the PR body with an accurate summary generated from the actual diff.

> **Prompt your agent:**
>
> Use the `prdiffer` skill to fetch the diff for `https://github.com/<owner>/<repository>/pull/<pull_number>`, then generate a clear, structured PR description covering the motivation, a summary of changed files, and a testing checklist. Update the PR body with the generated description.

The agent will call `prdiffer__get_pr_diff` to understand what changed, compose a Markdown description (motivation, file-level summary, testing notes), and submit it via `prdiffer__describe_pr`.

---

> **Tip:** You can combine all three in a single prompt:
>
> *"Fetch the diff for `https://github.com/<owner>/<repo>/pull/<number>`, write a comprehensive review, generate a structured description and update the PR body, then approve it if everything looks good."*
>

## GitLab strict full-diff

GitLab merge-request diffs are reconstructed from one immutable MR diff version
(version ID + base/start/head SHAs). Successful responses are ordered,
full-context, and all-or-nothing. Nested namespaces such as
`https://gitlab.com/group/subgroup/project/-/merge_requests/42` are supported
on GitLab.com and custom-hosted GitLab (e.g. `https://gitlab.example.com/group/project/-/merge_requests/1`). Binary, oversized, unavailable, or incomplete inventories
fail the entire request with `E5020_FULL_DIFF_INCOMPLETE` (structured MCP
`ToolError` JSON). Legacy hunk-only cache keys under `gitlab:owner:repo:iid`
are ignored; strict cache identity uses `gitlab-full-diff-v1:...`.

Configure via `gitlab.*` settings (`timeout`, `max_retries`, `max_concurrent`,
`retry_transient_errors`, `obey_rate_limit`, `max_file_size_bytes`) plus shared
`app.max_files_allowed`, `diff.max_total_chars`, and
`mcp.pr_diff_request_timeout_seconds`. Override file admission and the
aggregate `RESPONSE_SIZE_LIMIT` budget at runtime with `MAX_FILES_ALLOWED` and
`MAX_TOTAL_CHARS` in `.env` (see `.env.example` / `start-prdiffer-mcp-server.sh`).
