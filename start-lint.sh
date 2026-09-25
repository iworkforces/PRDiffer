#!/bin/bash

set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"
RUN_RUFF=(uv run --project "$SCRIPT_DIR" --frozen --no-sync --no-python-downloads ruff)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
PYTHON_FILES_PATTERN="*.py"
RUFF_CONFIG_FILE="pyproject.toml"
VENV_DIR="venv"

echo -e "${BLUE}🔍 PRDiffer - Code Linting${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""

# Function to check if uv is installed
check_uv() {
    if ! command -v uv &> /dev/null; then
        echo -e "${RED}❌ uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/${NC}" >&2
        exit 1
    fi
}

check_ruff() {
    local version
    if version=$("${RUN_RUFF[@]}" --version 2>/dev/null); then
        echo -e "${GREEN}✅ ruff is available${NC}"
        echo -e "${CYAN}Version: $version${NC}"
        return 0
    fi

    echo -e "${RED}❌ ruff is unavailable in the project environment. Run uv sync --frozen --group dev in $SCRIPT_DIR, then retry.${NC}" >&2
    exit 1
}

# Function to find Python files
find_python_files() {
    echo -e "${BLUE}🔍 Finding Python files...${NC}"

    # Find all Python files, excluding common directories to ignore
    PYTHON_FILES=$(find . -name "*.py" \
        -not -path "./venv/*" \
        -not -path "./env/*" \
        -not -path "./.venv/*" \
        -not -path "./build/*" \
        -not -path "./dist/*" \
        -not -path "./.git/*" \
        -not -path "./__pycache__/*" \
        -not -path "./.pytest_cache/*" \
        -not -path "./node_modules/*" \
        | sort)

    if [ -z "$PYTHON_FILES" ]; then
        echo -e "${YELLOW}⚠️  No Python files found${NC}"
        exit 0
    fi

    FILE_COUNT=$(echo "$PYTHON_FILES" | wc -l)
    echo -e "${GREEN}✅ Found $FILE_COUNT Python files${NC}"
    echo ""
}

# Function to show ruff configuration
show_config() {
    echo -e "${BLUE}⚙️  Ruff Configuration:${NC}"

    if [ -f "$RUFF_CONFIG_FILE" ]; then
        echo -e "${GREEN}✅ Using configuration from $RUFF_CONFIG_FILE${NC}"

        # Show relevant ruff configuration if it exists
        if grep -q "\[tool.ruff\]" "$RUFF_CONFIG_FILE" 2>/dev/null; then
            echo -e "${CYAN}Configuration preview:${NC}"
            sed -n '/\[tool\.ruff\]/,/^\[/p' "$RUFF_CONFIG_FILE" | head -20
        fi
    else
        echo -e "${YELLOW}⚠️  No ruff configuration found, using defaults${NC}"
        echo -e "${CYAN}Default rules: E, W, F (pycodestyle errors, warnings, pyflakes)${NC}"
    fi
    echo ""
}

# Function to run ruff check (dry run)
run_check() {
    echo -e "${BLUE}🔍 Running ruff check (dry run)...${NC}"
    echo ""

    if "${RUN_RUFF[@]}" check . --output-format=full; then
        echo ""
        echo -e "${GREEN}✅ No linting issues found!${NC}"
        return 0
    else
        echo ""
        echo -e "${YELLOW}⚠️  Linting issues detected${NC}"
        return 1
    fi
}

# Function to run ruff check with statistics
run_check_stats() {
    echo -e "${BLUE}📊 Linting Statistics:${NC}"
    echo ""

    # Get statistics by rule
    echo -e "${CYAN}Issues by rule:${NC}"
    "${RUN_RUFF[@]}" check . --output-format=concise | cut -d: -f4 | cut -d' ' -f2 | sort | uniq -c | sort -nr || true
    echo ""

    # Get statistics by file
    echo -e "${CYAN}Files with issues:${NC}"
    "${RUN_RUFF[@]}" check . --output-format=concise | cut -d: -f1 | sort | uniq -c | sort -nr | head -10 || true
    echo ""
}

# Function to fix issues automatically
run_fix() {
    echo -e "${BLUE}🔧 Running ruff fix (automatic fixes)...${NC}"
    echo ""

    # Run ruff with --fix flag
    if "${RUN_RUFF[@]}" check . --fix; then
        echo ""
        echo -e "${GREEN}✅ Automatic fixes applied successfully${NC}"
    else
        echo ""
        echo -e "${YELLOW}⚠️  Some issues were fixed, but manual fixes may be needed${NC}"
    fi

    if [ "${1:-}" != "final-check-follows" ]; then
        echo ""
        echo -e "${BLUE}🔍 Checking for remaining issues...${NC}"
        if "${RUN_RUFF[@]}" check . --output-format=concise; then
            echo -e "${GREEN}✅ All fixable issues have been resolved${NC}"
        else
            echo -e "${YELLOW}⚠️  Some issues require manual attention${NC}"
        fi
    fi
}

# Function to replace triple double quotes with triple single quotes
replace_triple_quotes() {
    echo -e "${BLUE}🔄 Replacing triple double quotes with single quotes...${NC}"
    echo ""

    # Find all Python files
    find_python_files

    local count=0
    echo "${PYTHON_FILES}" | while read -r file; do
        if [ -n "${file}" ]; then
            # Check if file contains triple double quotes
            if grep -q '"""' "${file}"; then
                echo -e "${CYAN}Processing: ${file}${NC}"

                # Replace triple double quotes with triple single quotes
                if [[ "${OSTYPE}" == "darwin"* ]]; then
                    # macOS sed syntax
                    sed -i '' "s/\"\"\"/'''/g" "${file}"
                else
                    # Linux sed syntax
                    sed -i "s/\"\"\"/'''/g" "${file}"
                fi

                ((count++))
                echo -e "${GREEN}✅ Updated quotes in: ${file}${NC}"
            fi
        fi
    done

    echo ""
    if [ "$count" -gt 0 ]; then
        echo -e "${GREEN}✅ Replaced triple quotes in $count files${NC}"
    else
        echo -e "${YELLOW}ℹ️  No triple double quotes found to replace${NC}"
    fi
}

# Function to format code
run_format() {
    echo -e "${BLUE}🎨 Running ruff format...${NC}"
    echo ""

    # Run ruff format (ignore failures, whitespace will be stripped anyway)
    "${RUN_RUFF[@]}" format . || true

    # Remove trailing whitespace in all Python files
    find_python_files
    echo -e "${BLUE}🔧 Removing trailing spaces...${NC}"
    if [[ "${OSTYPE}" == "darwin"* ]]; then
        echo "${PYTHON_FILES}" | while read -r file; do
            [ -n "${file}" ] && sed -i '' -E 's/[[:space:]]+$//' "${file}"
        done
    else
        echo "${PYTHON_FILES}" | while read -r file; do
            [ -n "${file}" ] && sed -i -E 's/[[:space:]]+$//' "${file}"
        done
    fi

    echo ""
    echo -e "${GREEN}✅ Code formatting and trailing space removal completed${NC}"
}

# Function to show detailed help
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --check       Run linting check only (default)"
    echo "  --fix         Run linting check and apply automatic fixes"
    echo "  --format      Run code formatting (ruff format)"
    echo "  --quotes      Replace triple double quotes with single quotes"
    echo "  --all         Run check, fix, and format"
    echo "  --stats       Show detailed linting statistics"
    echo "  --config      Show ruff configuration"
    echo "  --files       List Python files that will be processed"
    echo "  --install     Install/upgrade ruff"
    echo "  --help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                    # Check for issues"
    echo "  $0 --fix             # Fix issues automatically"
    echo "  $0 --format          # Format code"
    echo "  $0 --quotes          # Replace triple double quotes"
    echo "  $0 --all             # Check, fix, and format"
    echo "  $0 --stats           # Show statistics"
    echo ""
    echo "Environment Variables:"
    echo "  RUFF_CONFIG    Path to ruff configuration file"
    echo "  RUFF_CACHE_DIR Directory for ruff cache"
    echo ""
    echo "Common ruff rules:"
    echo "  E             pycodestyle errors"
    echo "  W             pycodestyle warnings"
    echo "  F             pyflakes"
    echo "  I             isort (import sorting)"
    echo "  N             pep8-naming"
    echo "  UP            pyupgrade"
    echo "  B             flake8-bugbear"
    echo "  C4            flake8-comprehensions"
    echo "  SIM           flake8-simplify"
}

# Function to install/upgrade ruff
install_ruff() {
    echo -e "${BLUE}📦 Installing/upgrading ruff...${NC}"
    uv pip install --python "$SCRIPT_DIR/.venv/bin/python" --upgrade ruff

    echo -e "${GREEN}✅ ruff installation completed${NC}"
    check_ruff
}

# Function to list files
list_files() {
    echo -e "${BLUE}📁 Python files to be processed:${NC}"
    echo ""

    find_python_files

    echo -e "${CYAN}Files:${NC}"
    echo "$PYTHON_FILES" | while read -r file; do
        if [ -n "$file" ]; then
            echo "  $file"
        fi
    done
    echo ""
    echo -e "${GREEN}Total: $FILE_COUNT files${NC}"
}

# Main execution
main() {
    # Parse command line arguments
    ACTION="check"
    SHOW_STATS=false

    while [[ $# -gt 0 ]]; do
        case $1 in
            --check)
                ACTION="check"
                shift
                ;;
            --fix)
                ACTION="fix"
                shift
                ;;
            --format)
                ACTION="format"
                shift
                ;;
            --quotes)
                ACTION="quotes"
                shift
                ;;
            --all)
                ACTION="all"
                shift
                ;;
            --stats)
                SHOW_STATS=true
                shift
                ;;
            --config)
                check_uv
                check_ruff
                show_config
                exit 0
                ;;
            --files)
                list_files
                exit 0
                ;;
            --install)
                check_uv
                install_ruff
                exit 0
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                echo -e "${RED}Unknown option: $1${NC}"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done

    # Execute main workflow
    check_uv
    check_ruff
    show_config

    case $ACTION in
        "check")
            if run_check; then
                if [ "$SHOW_STATS" = true ]; then
                    run_check_stats
                fi
                echo -e "${GREEN}🎉 Linting check completed successfully${NC}"
            else
                if [ "$SHOW_STATS" = true ]; then
                    run_check_stats
                fi
                echo ""
                echo -e "${YELLOW}💡 To fix issues automatically, run: $0 --fix${NC}"
                exit 1
            fi
            ;;
        "fix")
            run_fix
            if [ "$SHOW_STATS" = true ]; then
                run_check_stats
            fi
            echo -e "${GREEN}🎉 Linting fix completed${NC}"
            ;;
        "format")
            run_format
            echo -e "${GREEN}🎉 Code formatting completed${NC}"
            ;;
        "quotes")
            replace_triple_quotes
            echo -e "${GREEN}🎉 Triple quote replacement completed${NC}"
            ;;
        "all")
            echo -e "${PURPLE}🚀 Running complete linting workflow...${NC}"
            echo ""

            # Step 1: Check
            echo -e "${BLUE}Step 1: Initial check${NC}"
            run_check || true
            echo ""

            # Step 2: Fix
            echo -e "${BLUE}Step 2: Apply fixes${NC}"
            run_fix final-check-follows
            echo ""

            # Step 3: Format
            echo -e "${BLUE}Step 3: Format code${NC}"
            run_format
            echo ""

            # Step 4: Final check
            echo -e "${BLUE}Step 4: Final verification${NC}"
            final_status=0
            if run_check; then
                echo -e "${GREEN}🎉 Complete linting workflow finished successfully${NC}"
            else
                echo -e "${YELLOW}⚠️  Some issues may require manual attention${NC}"
                final_status=1
            fi

            if [ "$SHOW_STATS" = true ]; then
                echo ""
                run_check_stats
            fi
            return "$final_status"
            ;;
    esac
}

# Handle Ctrl+C gracefully
trap 'echo -e "\n${YELLOW}Linting interrupted${NC}"; exit 1' INT

# Run main function
main "$@"
