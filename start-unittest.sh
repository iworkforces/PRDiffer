#!/bin/bash

# PRDiffer - Unit Testing Script
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TEST_DIR="tests"
PYTEST_CONFIG_FILE="pyproject.toml"
COVERAGE_MIN="${COVERAGE_MIN:-80}"
UV_RUN=(uv run --project "$SCRIPT_DIR" --frozen --no-sync --no-python-downloads)

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

check_uv() {
    command -v uv >/dev/null 2>&1 || fail "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/ and provision the project with uv sync --frozen --group dev."
}

check_pytest() {
    "${UV_RUN[@]}" pytest --version >/dev/null 2>&1 || fail "pytest is unavailable in the existing project environment. Run uv sync --frozen --group dev."
}

find_test_files() {
    [[ -d "$TEST_DIR" ]] || fail "Test directory '$SCRIPT_DIR/$TEST_DIR' is missing. Restore the project tests."
    TEST_FILES=()
    while IFS= read -r -d '' file; do
        TEST_FILES+=("$file")
    done < <(find "$TEST_DIR" -type f \( -name 'test_*.py' -o -name '*_test.py' \) -print0)
    (( ${#TEST_FILES[@]} > 0 )) || fail "No test files found in '$SCRIPT_DIR/$TEST_DIR'. Add test_*.py or *_test.py files."
}

show_config() {
    if [[ -f "$PYTEST_CONFIG_FILE" ]] && grep -q '\[tool.pytest' "$PYTEST_CONFIG_FILE"; then
        printf 'Using pytest configuration from %s\n' "$PYTEST_CONFIG_FILE"
    elif [[ -f pytest.ini ]]; then
        printf 'Using pytest configuration from pytest.ini\n'
    else
        printf 'No pytest configuration found; using pytest defaults.\n'
    fi
}

show_help() {
    printf '%s\n' \
        "Usage: $0 [OPTIONS]" \
        '  --run         Run all tests (default)' \
        '  --coverage    Run tests with coverage analysis (threshold advisory only)' \
        '  --parallel    Run tests in parallel' \
        '  --watch       Re-run tests on changes (requires pytest-watch)' \
        '  --file FILE   Run a specific test file' \
        '  --pattern PAT Run tests matching a pattern' \
        '  --stats       Show test collection statistics' \
        '  --config      Show pytest configuration' \
        '  --list        List test files' \
        '  --install     Explicitly install/upgrade pytest dependencies' \
        '  --help        Show this help message' \
        "  COVERAGE_MIN  Advisory coverage target (default: 80)"
}

ACTION=run
TEST_FILE=
TEST_PATTERN=
while (( $# > 0 )); do
    case "$1" in
        --run|--coverage|--parallel|--watch|--stats|--config|--list|--install|--help)
            ACTION="${1#--}"
            shift
            ;;
        --file|--pattern)
            option="$1"
            (( $# >= 2 )) && [[ -n "$2" ]] || fail "Missing value for $option. Use --help for usage."
            if [[ "$option" == --file ]]; then
                ACTION=file
                TEST_FILE="$2"
            else
                ACTION=pattern
                TEST_PATTERN="$2"
            fi
            shift 2
            ;;
        *) fail "Unknown option: $1. Use --help for usage." ;;
    esac
done

if [[ "$ACTION" == help ]]; then
    show_help
    exit 0
fi

check_uv
if [[ "$ACTION" == install ]]; then
    uv pip install --python "$SCRIPT_DIR/.venv/bin/python" --upgrade pytest pytest-asyncio pytest-cov pytest-xdist pytest-watch
    exit 0
fi

find_test_files
check_pytest

case "$ACTION" in
    run)
        "${UV_RUN[@]}" pytest "$TEST_DIR" -v
        ;;
    coverage)
        printf 'Coverage target: %s%% (advisory; no CI threshold enforced).\n' "$COVERAGE_MIN"
        "${UV_RUN[@]}" pytest "$TEST_DIR" -v --cov=prdiffer --cov-report=term-missing --cov-report=html
        ;;
    parallel)
        "${UV_RUN[@]}" pytest "$TEST_DIR" -v -n auto
        ;;
    watch)
        "${UV_RUN[@]}" ptw "$TEST_DIR" -- -v || fail "Watch failed. Provision pytest-watch with uv sync --frozen --group dev or use --install."
        ;;
    file)
        [[ -f "$TEST_FILE" ]] || fail "Test file not found: $TEST_FILE"
        "${UV_RUN[@]}" pytest "$TEST_FILE" -v
        ;;
    pattern)
        "${UV_RUN[@]}" pytest "$TEST_DIR" -v -k "$TEST_PATTERN"
        ;;
    stats)
        printf 'Test collection:\n'
        "${UV_RUN[@]}" pytest "$TEST_DIR" --collect-only -q
        printf 'Test files: %s\n' "${#TEST_FILES[@]}"
        ;;
    config)
        show_config
        ;;
    list)
        printf 'Test files (%s):\n' "${#TEST_FILES[@]}"
        printf '  %s\n' "${TEST_FILES[@]}"
        ;;
esac
