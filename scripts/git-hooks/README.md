# Git Hooks

This directory contains git hooks for the PRDiffer project.

## Available Hooks

### pre-push

Provision the development environment from the repository root before running the hook:

```bash
uv sync --frozen --group dev
```

The hook runs read-only validation checks before allowing a push:

1. **Type Checking**: Runs `./start-type-check.sh --check`
   - Uses ty for static type checking

2. **Linting**: Runs `./start-lint.sh --check`
   - Checks code style with ruff

Neither check applies fixes or formats code. The check wrappers use the already-provisioned environment without syncing dependencies or downloading Python.

If either check fails, the push is blocked.

## Installation

Run the setup script from the repository root:

```bash
./scripts/setup-git-hooks.sh
```

This will copy all hooks from `scripts/git-hooks/` to `.git/hooks/` and make them executable.

## Manual Installation

Alternatively, you can manually copy and enable hooks:

```bash
cp scripts/git-hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

## Bypassing Hooks

In rare cases where you need to bypass the hooks (not recommended):

```bash
git push --no-verify
```

## Why Store Hooks Here?

Git hooks are normally stored in `.git/hooks/`, but that directory is not tracked by version control. By storing hooks in `scripts/git-hooks/`, we can:

- Version control the hooks
- Share them with all developers
- Ensure consistent validation across the team
- Update hooks for everyone through git pulls

## Updating Hooks

When hooks are updated in the repository:

1. Pull the latest changes: `git pull`
2. Re-run the setup script: `./scripts/setup-git-hooks.sh`

## Adding New Hooks

To add a new hook:

1. Create the hook file in `scripts/git-hooks/` (e.g., `pre-commit`, `commit-msg`)
2. Make it executable: `chmod +x scripts/git-hooks/your-hook`
3. Test it locally: `cp scripts/git-hooks/your-hook .git/hooks/`
4. Commit and push
5. Document it in this README
6. Ask team members to run `./scripts/setup-git-hooks.sh`
