# AGENTS.md - Scripts

Developer tooling: architecture analysis, full-diff benchmarks, git hooks.

## STRUCTURE
```
scripts/
├── analyze_dependencies.py    # Clean Architecture AST rule engine + CLI (~173)
├── bench_diff_generation.py   # Deterministic strict-v1 full-diff harness (git-tree fake repository)
├── setup-git-hooks.sh         # Install versioned hooks (82)
└── git-hooks/
    ├── pre-push               # Runs type-check + lint
    └── README.md
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Layer violations** | `analyze_dependencies.py` | `python3 scripts/analyze_dependencies.py --path prdiffer` |
| **Full-diff baseline/post** | `bench_diff_generation.py` | Matrix `strict-v1`; baseline + post worker modes |
| **Install hooks** | `setup-git-hooks.sh` | Copies into `.git/hooks/` |
| **Pre-push gates** | `git-hooks/pre-push` | `./start-type-check.sh --check` then `./start-lint.sh --check` (read-only) |

## FULL-DIFF BENCHMARK (`bench_diff_generation.py`)
- **Matrix `strict-v1`** (seed `5020`, 1 warmup excluded from samples):
  - `medium`: 25 files × 200 lines × 5 samples
  - `large`: 250 files × 1000 lines × 5 samples
  - `pathological`: 10 files × 5000 near-matching lines × 3 samples
- **Baseline modes**: `sync-current`, `async-current-negative-control` (records event-loop blocking; does not claim safety)
- **Post modes**: `serialized-worker-1`, `bounded-worker-2`, `bounded-worker-4` (available in `phase=post`; unsupported if requested during baseline)
- **Artifacts**: refuse overwrite by default; sealed baseline under `.omo/evidence/full-diff-correctness-performance/`
- **No network**: in-memory fake repository/content only
- Validity tests: `tests/performance/test_full_diff_benchmark.py` loads this script by path

```bash
# Capture sealed baseline
uv run python scripts/bench_diff_generation.py \
  --matrix strict-v1 --phase baseline \
  --modes sync-current,async-current-negative-control \
  --json .omo/evidence/full-diff-correctness-performance/task-1-full-diff-correctness-performance.baseline.json

# Post-change worker modes
uv run python scripts/bench_diff_generation.py \
  --matrix strict-v1 --phase post \
  --modes serialized-worker-1,bounded-worker-2,bounded-worker-4 \
  --json post-report.json

# Compare later post report
uv run python scripts/bench_diff_generation.py \
  --compare <baseline.json> <post.json> --json comparison.json
```

## ARCHITECTURE ANALYZER
- One stdlib AST rule engine shared by the CLI and `tests/unit/application/test_architecture.py` (loaded by path via `importlib.util.spec_from_file_location`; importing it has no CLI side effects).
- Public API: `scan_package(package_dir) -> ScanResult` (`dependencies`, `violations: list[ImportViolation]`, `parse_failures: list[ParseFailure]`, `is_clean`) and `format_failures(result)` for `path:line: rule: ...` messages.
- Walks the whole AST: imports at any depth (function bodies, methods, class bodies, `if`/`try` blocks) count.
- Covers `import x`, `from x import y`, and relative forms resolved against the module's package (`__init__.py` anchors to its own package; `from .. import infrastructure` resolves to `prdiffer.infrastructure`). Layer matching uses namespace boundaries, not raw prefixes.
- Forbids Domain→Application, Domain→Infrastructure, Application→Infrastructure.
- Only the exact path `prdiffer/application/factory.py` (composition root) is exempt, and only from Application→Infrastructure; any other `factory.py` is checked normally.
- Fails closed: unparseable/undecodable source and relative imports that climb above the top package are failures.
- Exit 0 only when clean; exit 1 on any violation or failure (also when `--path` is missing).

## CI / HOOKS RELATIONSHIP
- PR CI (`.github/workflows/pr-quality.yml` on `main`/`develop`): matrix jobs for `uv run ruff check .`, `uv run ty check`, `uv run python scripts/analyze_dependencies.py --path prdiffer` (Architecture), `uv run pytest tests -v --tb=short` after `uv sync --frozen --group dev`.
- Pre-push: type-check + lint only (not full pytest) via versioned hooks.

## CONVENTIONS
- Hooks are version-controlled under `scripts/git-hooks/`; re-run setup after pull if hooks change.
- Benchmark fixtures must stay deterministic (stable digests across baseline/post).
- Prefer non-interactive flags in hooks/scripts (no `git -i`).

## ANTI-PATTERNS
- NO editing `.git/hooks/` without updating `scripts/git-hooks/`.
- NO live network in benches.
- NO overwriting a sealed baseline without an intentional new path.
- NO claiming event-loop safety from `async-current-negative-control`.
