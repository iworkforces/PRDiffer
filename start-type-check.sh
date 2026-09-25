#!/bin/bash

# PRDiffer - Type Checking Script
# This script uses ty (Astral's fast Python type checker) to perform static type checking

set -e
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"
TY_COMMAND=(uv run --project "$SCRIPT_DIR" --frozen --no-sync --no-python-downloads ty)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
TY_CONFIG_FILE="pyproject.toml"

echo -e "${BLUE}🔍 PRDiffer - Type Checking${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""

# Function to check if uv is installed
check_uv() {
    if ! command -v uv &> /dev/null; then
        echo -e "${RED}❌ uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/${NC}" >&2
        return 1
    fi

    echo -e "${GREEN}✅ uv is available${NC}"
    echo -e "${CYAN}Version: $(uv --version)${NC}"
}

# Function to check if ty is installed
check_ty() {
    local version
    if ! version=$("${TY_COMMAND[@]}" version 2>&1); then
        echo -e "${RED}❌ ty is unavailable in the project environment: $version${NC}" >&2
        echo -e "${YELLOW}Provision it with uv sync --frozen --group dev in $SCRIPT_DIR${NC}" >&2
        return 1
    fi

    echo -e "${GREEN}✅ ty is available${NC}"
    echo -e "${CYAN}Version: $version${NC}"
}

# Function to show ty configuration
show_config() {
    echo -e "${BLUE}⚙️  ty Configuration:${NC}"

    if [ -f "$TY_CONFIG_FILE" ]; then
        echo -e "${GREEN}✅ Using configuration from $TY_CONFIG_FILE${NC}"

        # Show relevant ty configuration if it exists
        if grep -q "\[tool.ty\]" "$TY_CONFIG_FILE" 2>/dev/null; then
            echo -e "${CYAN}Configuration preview:${NC}"
            sed -n '/\[tool\.ty\]/,/^\[tool\.[^t]/p' "$TY_CONFIG_FILE" | head -30
        fi
    else
        echo -e "${YELLOW}⚠️  No ty configuration found, using defaults${NC}"
    fi
    echo ""
}

# Function to run ty type check
run_type_check() {
    echo -e "${BLUE}🔍 Running ty type check...${NC}"
    echo ""

    # Run ty with detailed output
    if "${TY_COMMAND[@]}" check; then
        echo ""
        echo -e "${GREEN}✅ No type errors found!${NC}"
        return 0
    else
        echo ""
        echo -e "${YELLOW}⚠️  Type errors detected${NC}"
        return 1
    fi
}

# Function to run ty type check in strict mode
run_type_check_strict() {
    echo -e "${BLUE}🔍 Running ty strict type check (warnings as errors)...${NC}"
    echo ""

    # Run ty with --error-on-warning flag
    if "${TY_COMMAND[@]}" check --error-on-warning; then
        echo ""
        echo -e "${GREEN}✅ No type errors or warnings found!${NC}"
        return 0
    else
        echo ""
        echo -e "${YELLOW}⚠️  Type errors or warnings detected${NC}"
        return 1
    fi
}

# Function to show type check statistics
show_stats() {
    echo -e "${BLUE}📊 Type Check Statistics:${NC}"
    echo ""

    # Get statistics by running ty and counting errors
    echo -e "${CYAN}Running type check for statistics...${NC}"
    local output
    output=$("${TY_COMMAND[@]}" check 2>&1) || true

    # Count errors by type
    echo -e "${CYAN}Error summary:${NC}"
    echo "$output" | grep -E "error\[" | sed 's/.*error\[\([^]]*\)\].*/\1/' | sort | uniq -c | sort -nr || echo "No errors found"
    echo ""

    # Count errors by file
    echo -e "${CYAN}Files with errors:${NC}"
    echo "$output" | grep -E "^[^ ].*\.py:" | cut -d: -f1 | sort | uniq -c | sort -nr | head -10 || echo "No errors found"
    echo ""
}

# Function to run type check in watch mode
run_watch() {
    echo -e "${BLUE}👀 Running ty in watch mode...${NC}"
    echo -e "${CYAN}Press Ctrl+C to stop watching${NC}"
    echo ""

    "${TY_COMMAND[@]}" check --watch
}

# Function to show detailed help
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --check       Run type checking (default)"
    echo "  --strict      Run type checking in strict mode (warnings as errors)"
    echo "  --stats       Show detailed type error statistics"
    echo "  --watch       Run in watch mode (re-check on file changes)"
    echo "  --config      Show ty configuration"
    echo "  --install     Install/upgrade ty"
    echo "  --help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                    # Run type check"
    echo "  $0 --strict           # Run strict type check (warnings as errors)"
    echo "  $0 --stats           # Show statistics"
    echo "  $0 --watch           # Watch mode"
    echo ""
}

# Function to install/upgrade ty
install_ty() {
    echo -e "${BLUE}📦 Installing/upgrading ty...${NC}"
    echo -e "${CYAN}Installing ty via uv...${NC}"
    uv pip install --upgrade ty

    echo -e "${GREEN}✅ ty installation completed${NC}"
    check_ty
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
            --strict)
                ACTION="strict"
                shift
                ;;
            --stats)
                SHOW_STATS=true
                shift
                ;;
            --watch)
                ACTION="watch"
                shift
                ;;
            --config)
                check_uv
                check_ty
                show_config
                exit 0
                ;;
            --install)
                check_uv
                install_ty
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
    check_ty
    show_config

    case $ACTION in
        "check")
            if run_type_check; then
                if [ "$SHOW_STATS" = true ]; then
                    show_stats
                fi
                echo -e "${GREEN}🎉 Type checking completed successfully${NC}"
            else
                if [ "$SHOW_STATS" = true ]; then
                    show_stats
                fi
                echo ""
                echo -e "${YELLOW}💡 Review the errors above and fix type issues${NC}"
                exit 1
            fi
            ;;
        "strict")
            if run_type_check_strict; then
                if [ "$SHOW_STATS" = true ]; then
                    show_stats
                fi
                echo -e "${GREEN}🎉 Strict type checking completed successfully${NC}"
            else
                if [ "$SHOW_STATS" = true ]; then
                    show_stats
                fi
                echo ""
                echo -e "${YELLOW}💡 Review the errors/warnings above and fix type issues${NC}"
                exit 1
            fi
            ;;
        "watch")
            run_watch
            ;;
    esac
}

# Handle Ctrl+C gracefully
trap 'echo -e "\n${YELLOW}Type checking interrupted${NC}"; exit 1' INT

# Run main function
main "$@"
