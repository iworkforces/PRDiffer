import sys
import os
import argparse
from pathlib import Path

# Add the current directory to Python path for direct execution
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prdiffer.version import __version__
from prdiffer.application.factory import create_mcp_server
from prdiffer.application.startup_config import StartupOverrides, resolve_mcp_server_config
from prdiffer.domain.exceptions import ConfigurationError
from prdiffer.infrastructure.settings import get_settings_service, load_project_dotenv
from prdiffer.infrastructure.cache.service import get_cache_service
from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.github_repository import GitHubPRDiffRepository
from prdiffer.infrastructure.factories.infrastructure_factory import InfrastructureFactory


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed command-line arguments
    """
    parser = argparse.ArgumentParser(
        prog="prdiffer",
        description=(
            "MCP Server for GitHub PR Review Process with Full Contexts. "
            "Precedence: CLI > MCP_* env (including project .env) > active Dynaconf mcp.* > defaults."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Shipped settings.toml selects HTTP when nothing overrides it
  prdiffer

  # Run as HTTP server
  prdiffer --transport http --port 9102

  # Run with SSE transport
  prdiffer --transport sse --port 9102 --host 0.0.0.0

Environment Variables:
  GITHUB_TOKEN    GitHub personal access token for API authentication
  MCP_TRANSPORT   Override transport mode (http, stdio, sse, streamable-http)
  MCP_PORT        Override server port
  MCP_HOST        Override server host
  MCP_PATH        Override server path

Precedence: CLI flags > MCP_* environment (including project .env) >
active Dynaconf mcp.* settings > defaults (http, 127.0.0.1, 9102, /mcp).
The shipped settings.toml sets mcp.transport = "http".
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version and exit",
    )

    parser.add_argument(
        "--transport",
        type=str,
        help="Transport protocol: stdio, http, sse, streamable-http (shipped settings default: http)",
    )

    parser.add_argument(
        "--port",
        type=str,
        help="Server port for non-stdio transports (default: 9102)",
    )

    parser.add_argument(
        "--host",
        type=str,
        help="Server host for non-stdio transports (default: 127.0.0.1)",
    )

    parser.add_argument(
        "--path",
        type=str,
        help="Server path for non-stdio transports (default: /mcp)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point for the MCP server.

    Resolve CLI > MCP_* environment (including project .env) > active Dynaconf
    mcp.* settings > defaults once, before initialization. The shipped
    settings.toml selects HTTP when nothing overrides it. Stdio diagnostics
    go exclusively to stderr; invalid configuration exits with E5009.
    """
    # Parse command-line arguments
    args = parse_args()

    overrides = StartupOverrides(transport=args.transport, port=args.port, host=args.host, path=args.path)

    # Load project-root .env (cwd-independent) so GITHUB_IGNORE_PATTERNS / tokens apply
    # when MCP is started outside the repository working directory.
    loaded_env = load_project_dotenv(override=False)
    if loaded_env is None:
        # Fallback: cwd-relative .env for non-checkout layouts
        from dotenv import load_dotenv

        cwd_env = Path.cwd() / ".env"
        if cwd_env.is_file():
            load_dotenv(cwd_env, override=False)
            loaded_env = cwd_env

    # Initialize dependencies following clean architecture principles
    settings_service = get_settings_service()
    try:
        config = resolve_mcp_server_config(settings_service, overrides=overrides)
    except ConfigurationError as e:
        print(f"❌ {e.error_code}: {e.message}", file=sys.stderr)
        sys.exit(2)

    output_stream = sys.stderr if config.is_stdio else sys.stdout
    print("🚀 Starting MCP Server For Fetching GitHub PR's Diff...", file=output_stream)
    print(f"📦 Version: {__version__}", file=output_stream)
    print(f"🔧 Transport: {config.transport}", file=output_stream)
    if loaded_env is not None:
        print(f"📄 Loaded environment from {loaded_env}", file=output_stream)

    logger = get_logger(transport=config.transport)
    cache_service = get_cache_service()

    # Top-level exception handler for graceful shutdown
    try:
        # Create server using factory pattern for proper dependency injection
        server = create_mcp_server(
            github_repository_class=GitHubPRDiffRepository,
            settings_service=settings_service,
            cache_service=cache_service,
            logger=logger,
            mcp_config=config,
            gitlab_reader=InfrastructureFactory().create_gitlab_session_reader(
                private_token=os.getenv("GITLAB_TOKEN") or None,
            ),
        )
        server.run()
    except KeyboardInterrupt:
        # Allow graceful exit on Ctrl+C
        print("\n⚠️  Server shutdown requested by user", file=output_stream)
        sys.exit(0)
    except SystemExit as e:
        # Allow sys.exit() to propagate
        if e.code != 0:
            logger.critical(f"Server exiting with code {e.code}")
        sys.exit(e.code)
    except Exception as e:
        # Catch-all for any other unhandled exceptions
        logger.critical(
            "Server crashed with unhandled exception",
            exc_info=True,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        print(f"❌ Fatal error: {type(e).__name__}: {e}", file=output_stream)
        sys.exit(1)


if __name__ == "__main__":
    main()
