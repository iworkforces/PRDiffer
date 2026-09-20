#!/bin/bash

set -euo pipefail

# PRDiffer MCP Server Startup Script
#
# Usage:
#   ./start-prdiffer-mcp-server.sh [OPTIONS]
#
# Options:
#   --transport MODE    Transport: http, sse, stdio, or streamable-http
#                       (default: TRANSPORT or http)
#   --port PORT         ASCII decimal port from 1 through 65535
#                       (default: PORT or 9102; validated but omitted for stdio)
#   --verbose, -v       Enable debug output on stderr
#   --help, -h          Show this help message
#
# Environment Variables:
#   TRANSPORT              Default transport; command-line options take precedence
#   PORT                   Default port; command-line options take precedence
#   GITHUB_TOKEN           GitHub personal access token (one provider token is required)
#   GITLAB_TOKEN           GitLab personal access token (one provider token is required)
#   GITLAB_ALLOWED_HOSTS   Comma-separated GitLab host allowlist (default: gitlab.com)
#   MAX_FILES_ALLOWED      Selected-file limit (default: settings.toml value 50)
#   MAX_TOTAL_CHARS        Aggregate diff budget (default: settings.toml value 600000)
#   GITHUB_IGNORE_PATTERNS Comma-separated GitHub ignore globs
#   PID_FILE               PID file location (default: .prdiffer-server.pid)
#   ENV_FILE               Trusted shell environment file (default: project .env)
#
# Provisioning:
#   Install uv and run `uv sync --frozen` before using this launcher.
#   The launcher requires pyproject.toml, uv.lock, and prdiffer/server.py. It does
#   not install uv, download Python, update the lockfile, or sync dependencies.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TRANSPORT_SELECTION="http"
AMBIENT_TRANSPORT_SET=false
if [[ -n "${TRANSPORT+x}" ]]; then
    AMBIENT_TRANSPORT_SET=true
    TRANSPORT_SELECTION="$TRANSPORT"
fi

PORT_SELECTION="9102"
AMBIENT_PORT_SET=false
if [[ -n "${PORT+x}" ]]; then
    AMBIENT_PORT_SET=true
    PORT_SELECTION="$PORT"
fi

VERBOSE_SELECTION=false
SHOW_HELP=false

show_help() {
    cat >&2 <<'EOF'
Usage:
  ./start-prdiffer-mcp-server.sh [OPTIONS]

Options:
  --transport MODE    Transport: http, sse, stdio, or streamable-http
                      (default: TRANSPORT or http)
  --port PORT         ASCII decimal port from 1 through 65535
                      (default: PORT or 9102; validated but omitted for stdio)
  --verbose, -v       Enable debug output on stderr
  --help, -h          Show this help message

The launcher requires a provisioned uv project. It never installs uv, downloads
Python, updates uv.lock, or syncs dependencies.
EOF
}

usage_error() {
    printf 'Error: %s\n' "$1" >&2
    printf 'Run %s --help for usage.\n' "$0" >&2
    exit 2
}

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --transport)
                if [[ $# -lt 2 ]]; then
                    usage_error "--transport requires an operand"
                fi
                if [[ "$2" == --* ]]; then
                    usage_error "--transport requires an operand"
                fi
                if ! validate_transport "$2"; then
                    usage_error "unsupported transport: $2"
                fi
                TRANSPORT_SELECTION="$2"
                shift 2
                ;;
            --port)
                if [[ $# -lt 2 ]]; then
                    usage_error "--port requires an operand"
                fi
                if [[ "$2" == --* ]]; then
                    usage_error "--port requires an operand"
                fi
                if ! validate_port "$2"; then
                    usage_error "port must be an ASCII decimal value from 1 through 65535: $2"
                fi
                PORT_SELECTION="$2"
                shift 2
                ;;
            --verbose|-v)
                VERBOSE_SELECTION=true
                shift
                ;;
            --help|-h)
                SHOW_HELP=true
                shift
                ;;
            --*)
                usage_error "unknown option: $1"
                ;;
            *)
                usage_error "positional arguments are not supported: $1"
                ;;
        esac
    done
}

validate_transport() {
    case "$1" in
        http|sse|stdio|streamable-http)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

validate_port() {
    local candidate="$1"
    local normalized

    case "$candidate" in
        ""|*[!0123456789]*)
            return 1
            ;;
    esac

    normalized="$candidate"
    while [[ "${#normalized}" -gt 1 && "${normalized#0}" != "$normalized" ]]; do
        normalized="${normalized#0}"
    done

    if [[ "${#normalized}" -gt 5 ]]; then
        return 1
    fi

    [[ "$normalized" -ge 1 && "$normalized" -le 65535 ]]
}

if [[ "$AMBIENT_TRANSPORT_SET" == true ]] && ! validate_transport "$TRANSPORT_SELECTION"; then
    usage_error "unsupported ambient TRANSPORT: $TRANSPORT_SELECTION"
fi
if [[ "$AMBIENT_PORT_SET" == true ]] && ! validate_port "$PORT_SELECTION"; then
    usage_error "ambient PORT must be an ASCII decimal value from 1 through 65535: $PORT_SELECTION"
fi

parse_arguments "$@"

if ! validate_transport "$TRANSPORT_SELECTION"; then
    usage_error "unsupported transport: $TRANSPORT_SELECTION"
fi
if ! validate_port "$PORT_SELECTION"; then
    usage_error "port must be an ASCII decimal value from 1 through 65535: $PORT_SELECTION"
fi

SELECTED_TRANSPORT="$TRANSPORT_SELECTION"
SELECTED_PORT="$PORT_SELECTION"
SELECTED_VERBOSE="$VERBOSE_SELECTION"

if [[ "$SHOW_HELP" == true ]]; then
    show_help
    exit 0
fi

for required_file in pyproject.toml uv.lock prdiffer/server.py; do
    if [[ ! -f "$SCRIPT_DIR/$required_file" ]]; then
        printf 'Error: required project file not found: %s\n' "$SCRIPT_DIR/$required_file" >&2
        exit 1
    fi
done

if ! command -v uv >/dev/null 2>&1; then
    printf 'Error: uv is required; install it and provision the project before launching.\n' >&2
    exit 1
fi

ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
PID_FILE="${PID_FILE:-.prdiffer-server.pid}"
SERVER_PID=""
SERVER_REAPED=false
PID_FILE_OWNED=false
CLEANUP_STARTED=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() {
    printf '%b\n' "${BLUE}[INFO] $*${NC}" >&2
}

log_success() {
    printf '%b\n' "${GREEN}[OK] $*${NC}" >&2
}

log_warning() {
    printf '%b\n' "${YELLOW}[WARN] $*${NC}" >&2
}

log_error() {
    printf '%b\n' "${RED}[ERROR] $*${NC}" >&2
}

log_debug() {
    if [[ "$SELECTED_VERBOSE" == true ]]; then
        printf '%b\n' "${CYAN}[DEBUG] $*${NC}" >&2
    fi
}

is_valid_pid() {
    local candidate="$1"
    local normalized

    case "$candidate" in
        ""|*[!0123456789]*)
            return 1
            ;;
    esac

    normalized="$candidate"
    while [[ "${#normalized}" -gt 1 && "${normalized#0}" != "$normalized" ]]; do
        normalized="${normalized#0}"
    done
    [[ "$normalized" != "0" && "${#normalized}" -le 10 && "$normalized" -le 2147483647 ]]
}

# shellcheck disable=SC2329
remove_owned_pid_file() {
    local recorded_pid=""

    if [[ "$PID_FILE_OWNED" != true || ! -f "$PID_FILE" ]]; then
        return
    fi
    IFS= read -r recorded_pid < "$PID_FILE" || true
    if [[ "$recorded_pid" == "$SERVER_PID" ]]; then
        rm -f "$PID_FILE"
        log_debug "Removed owned PID file: $PID_FILE"
    else
        log_warning "Leaving PID file that no longer belongs to this launcher: $PID_FILE"
    fi
}

# shellcheck disable=SC2329
cleanup() {
    local exit_code=$?
    local count=0

    trap - EXIT INT TERM
    if [[ "$CLEANUP_STARTED" == true ]]; then
        return "$exit_code"
    fi
    CLEANUP_STARTED=true

    if is_valid_pid "$SERVER_PID" && [[ "$SERVER_REAPED" != true ]]; then
        if kill -0 "$SERVER_PID" 2>/dev/null; then
            log_warning "Shutting down PRDiffer MCP Server gracefully..."
            kill -TERM "$SERVER_PID" 2>/dev/null || true
            while [[ $count -lt 10 ]] && kill -0 "$SERVER_PID" 2>/dev/null; do
                sleep 1
                count=$((count + 1))
            done
            if kill -0 "$SERVER_PID" 2>/dev/null; then
                log_error "Force stopping server after 10 seconds..."
                kill -KILL "$SERVER_PID" 2>/dev/null || true
            fi
        fi
        wait "$SERVER_PID" 2>/dev/null || true
        SERVER_REAPED=true
    fi

    remove_owned_pid_file
    return "$exit_code"
}

# shellcheck disable=SC2329
handle_signal() {
    exit "$1"
}

load_env_file() {
    if [[ -f "$ENV_FILE" ]]; then
        log_info "Loading environment variables from $ENV_FILE"
        set -a
        # shellcheck source=/dev/null
        source "$ENV_FILE"
        set +a
    fi

    TRANSPORT="$SELECTED_TRANSPORT"
    PORT="$SELECTED_PORT"
}

check_existing_server() {
    local existing_pid=""
    local reply=""

    if [[ ! -f "$PID_FILE" ]]; then
        return
    fi

    existing_pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if is_valid_pid "$existing_pid" && kill -0 "$existing_pid" 2>/dev/null; then
        log_warning "Server is already running with PID: $existing_pid"
        printf 'Do you want to stop the existing server and start a new one? [y/N] ' >&2
        if IFS= read -r -n 1 reply; then
            printf '\n' >&2
        else
            reply=""
            printf '\n' >&2
        fi
        if [[ "$reply" == "y" || "$reply" == "Y" ]]; then
            log_info "Stopping existing server..."
            kill -TERM "$existing_pid" 2>/dev/null || true
            sleep 2
            if kill -0 "$existing_pid" 2>/dev/null; then
                kill -KILL "$existing_pid" 2>/dev/null || true
            fi
            rm -f "$PID_FILE"
        else
            log_info "Exiting. Use 'kill $existing_pid' to stop the existing server."
            exit 0
        fi
    else
        rm -f "$PID_FILE"
        log_debug "Removed stale or malformed PID file"
    fi
}

health_check() {
    local max_attempts=30
    local attempt=0

    log_info "Waiting for server to start..."
    while [[ $attempt -lt $max_attempts ]]; do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            log_error "Server process exited unexpectedly"
            return 1
        fi

        if [[ "$SELECTED_TRANSPORT" == "stdio" ]]; then
            log_success "Server process is running (PID: $SERVER_PID)"
            return 0
        fi
        if command -v nc >/dev/null 2>&1 && nc -z localhost "$SELECTED_PORT" 2>/dev/null; then
            log_success "Server is running on port $SELECTED_PORT"
            return 0
        fi
        if ! command -v nc >/dev/null 2>&1 && command -v lsof >/dev/null 2>&1 && lsof -i ":$SELECTED_PORT" >/dev/null 2>&1; then
            log_success "Server is running on port $SELECTED_PORT"
            return 0
        fi
        if ! command -v nc >/dev/null 2>&1 && ! command -v lsof >/dev/null 2>&1; then
            log_success "Server process is running (PID: $SERVER_PID)"
            return 0
        fi

        attempt=$((attempt + 1))
        sleep 1
    done

    log_warning "Server health check timed out, but process may still be starting"
    return 0
}

render_command() {
    local argument
    local escaped
    local rendered=""

    for argument in "${SERVER_CMD[@]}"; do
        printf -v escaped '%q' "$argument"
        rendered="${rendered}${rendered:+ }${escaped}"
    done
    printf '%s' "$rendered"
}

trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

printf '%b\n' "${BLUE}PRDiffer MCP Server${NC}" >&2
printf '%b\n\n' "${BLUE}=====================${NC}" >&2

load_env_file

if [[ -z "${GITHUB_TOKEN:-}" && -z "${GITLAB_TOKEN:-}" ]]; then
    log_error "Either GITHUB_TOKEN or GITLAB_TOKEN environment variable must be set"
    printf '%b\n' "${YELLOW}See the Authentication section in README.md.${NC}" >&2
    exit 1
fi

check_existing_server

UV_RUN=(
    uv run
    --project "$SCRIPT_DIR"
    --frozen
    --no-sync
    --no-python-downloads
    --no-env-file
)

if ! PYTHON_VERSION=$("${UV_RUN[@]}" python --version 2>&1); then
    log_error "The provisioned project Python environment is unavailable"
    exit 1
fi
log_info "Python version: $PYTHON_VERSION"

SERVER_CMD=(
    "${UV_RUN[@]}"
    python "$SCRIPT_DIR/prdiffer/server.py"
    --transport "$SELECTED_TRANSPORT"
)
if [[ "$SELECTED_TRANSPORT" != "stdio" ]]; then
    SERVER_CMD+=(--port "$SELECTED_PORT")
fi

log_info "Starting PRDiffer MCP Server..."
log_debug "Command: $(render_command)"
log_info "Press Ctrl+C to stop the server gracefully"

"${SERVER_CMD[@]}" <&0 &
SERVER_PID=$!
if ! printf '%s\n' "$SERVER_PID" > "$PID_FILE"; then
    log_error "Unable to write PID file: $PID_FILE"
    exit 1
fi
PID_FILE_OWNED=true
log_debug "Saved PID ($SERVER_PID) to $PID_FILE"

sleep 1
if ! health_check; then
    set +e
    wait "$SERVER_PID"
    CHILD_STATUS=$?
    set -e
    SERVER_REAPED=true
    if [[ $CHILD_STATUS -eq 0 ]]; then
        CHILD_STATUS=1
    fi
    exit "$CHILD_STATUS"
fi

set +e
wait "$SERVER_PID"
CHILD_STATUS=$?
set -e
SERVER_REAPED=true
exit "$CHILD_STATUS"
