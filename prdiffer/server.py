import sys
import os
import argparse
from pathlib import Path

# Add the current directory to Python path for direct execution
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prdiffer.version import __version__
from prdiffer.application.factory import create_mcp_server
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
        description="MCP Server for GitHub PR Review Process with Full Contexts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default http transport
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
        choices=["stdio", "http", "sse", "streamable-http"],
        help="Transport protocol (default: http, or from settings.toml)",
    )

    parser.add_argument(
        "--port",
        type=int,
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

    Supports both CLI argument parsing and environment variable configuration.
    When run as a tool (via `prdiffer` command), defaults to stdio transport.
    """
    # Parse command-line arguments
    args = parse_args()

    # Set environment variables based on CLI args or defaults
    # Priority: CLI args > existing env vars > default (http)
    if args.transport:
        os.environ["MCP_TRANSPORT"] = args.transport
    elif "MCP_TRANSPORT" not in os.environ:
        os.environ["MCP_TRANSPORT"] = "http"

    if args.port:
        os.environ["MCP_PORT"] = str(args.port)

    if args.host:
        os.environ["MCP_HOST"] = args.host

    if args.path:
        os.environ["MCP_PATH"] = args.path

    # Get the transport mode to determine where to print messages
    transport_mode = os.environ.get("MCP_TRANSPORT", "stdio")

    # In stdio mode, ALL output must go to stderr to avoid corrupting JSON-RPC protocol
    # Only JSON-RPC messages should go to stdout in stdio mode
    output_stream = sys.stderr if transport_mode == "stdio" else sys.stdout

    print("🚀 Starting MCP Server For Fetching GitHub PR's Diff...", file=output_stream)
    print(f"📦 Version: {__version__}", file=output_stream)
    print(f"🔧 Transport: {transport_mode}", file=output_stream)

    # Load project-root .env (cwd-independent) so GITHUB_IGNORE_PATTERNS / tokens apply
    # when MCP is started outside the repository working directory.
    loaded_env = load_project_dotenv(override=False)
    if loaded_env is not None:
        print(f"📄 Loaded environment from {loaded_env}", file=output_stream)
    else:
        # Fallback: cwd-relative .env for non-checkout layouts
        from dotenv import load_dotenv

        cwd_env = Path.cwd() / ".env"
        if cwd_env.is_file():
            load_dotenv(cwd_env, override=False)
            print(f"📄 Loaded environment from {cwd_env}", file=output_stream)

    # Initialize dependencies following clean architecture principles
    settings_service = get_settings_service()
    cache_service = get_cache_service()
    logger = get_logger()

    # Top-level exception handler for graceful shutdown
    try:
        # Create server using factory pattern for proper dependency injection
        server = create_mcp_server(
            github_repository_class=GitHubPRDiffRepository,
            settings_service=settings_service,
            cache_service=cache_service,
            logger=logger,
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
