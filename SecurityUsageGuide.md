# Security Usage Guide

This guide explains how to configure and use the authentication and security features of PRDiffer.

## Table of Contents

1. [Overview](#overview)
2. [Configuration](#configuration)
3. [Authentication Flow](#authentication-flow)
4. [Client Connection Guide](#client-connection-guide)
5. [Security Best Practices](#security-best-practices)
6. [Troubleshooting](#troubleshooting)

---

## Overview

PRDiffer implements API key-based authentication to control access to the MCP server. The authentication system includes:

- **API Key Authentication**: Requires clients to provide a valid API key
- **SHA-256 Hashing**: Keys are stored as SHA-256 hashes for security
- **Per-Client Rate Limiting**: Each client has independent rate limits
- **Admin and Regular Keys**: Different key types for different access levels

---

## Configuration

### Enabling Authentication

**IMPORTANT**: Authentication is **enabled by default** for production security. The `MCP_AUTH_ENABLED` environment variable controls this setting.

```bash
# Enable authentication (default for production)
export MCP_AUTH_ENABLED=true

# Disable authentication (ONLY for local development)
export MCP_AUTH_ENABLED=false
```

**Security Notice**: For production deployments, always keep authentication enabled (`MCP_AUTH_ENABLED=true`). Only disable authentication for local development environments where the server is not accessible from external networks.

### Configuring API Keys

API keys are configured through the `MCP_API_KEYS` environment variable. This is a comma-separated list of valid API keys.

```bash
# Set multiple API keys (comma-separated, no spaces)
export MCP_API_KEYS="sk_live_1234567890abcdef,sk_live_0987654321fedcba"

# Set a single API key
export MCP_API_KEYS="sk_live_1234567890abcdef"
```

### Admin API Keys

Admin keys have elevated privileges and are configured separately:

```bash
# Set admin API key
export MCP_ADMIN_API_KEY="sk_admin_abcdef1234567890"
```

### Configuration File Option

You can also set these values in your `.env` file:

```env
# .env
MCP_AUTH_ENABLED=true
MCP_API_KEYS=sk_live_1234567890abcdef,sk_live_0987654321fedcba
MCP_ADMIN_API_KEY=sk_admin_abcdef1234567890
```

### Complete Example

```bash
# .env file
GITHUB_TOKEN=ghp_your_github_token_here
MCP_AUTH_ENABLED=true
MCP_API_KEYS=client_key_abc123,client_key_def456,client_key_ghi789
MCP_ADMIN_API_KEY=admin_key_secret999
MCP_TRANSPORT=http
MCP_PORT=9102
MCP_HOST=127.0.0.1
```

---

## Authentication Flow

### How Authentication Works

1. **Client Request**: MCP client sends a request to the server with an `api_key` parameter

2. **Key Validation**: Server validates the API key against the list of configured keys

3. **Client Identification**: Server extracts client identifier from:
   - API key (if provided and valid)
   - X-API-Key header (if present)
   - X-Forwarded-For header (for proxied requests)
   - Remote address (fallback)

4. **Rate Limiting**: Server checks per-client rate limits

5. **Request Processing**: If authenticated, request is processed; otherwise, returns error

### Authentication States

| State | Description | Behavior |
|-------|-------------|----------|
| **Disabled** | `MCP_AUTH_ENABLED=false` | All requests allowed without authentication |
| **Enabled, No Key** | `MCP_AUTH_ENABLED=true`, client sends no key | Request rejected with error |
| **Enabled, Invalid Key** | Client sends invalid key | Request rejected with error |
| **Enabled, Valid Key** | Client sends valid key | Request processed normally |

### Key Hashing

All API keys are stored as SHA-256 hashes:

```
Original Key: sk_live_1234567890abcdef
SHA-256 Hash: 7f8a9b3c2d1e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a
```

This ensures that even if the server's memory is compromised, actual API keys cannot be extracted.

---

## Client Connection Guide

### Using the API Key Parameter

The `get_pr_diff` tool accepts an optional `api_key` parameter:

```python
from mcp import Client

async def main():
    # Connect to the MCP server
    async with Client("http://127.0.0.1:9102/mcp") as client:
        # Call the tool with API key
        result = await client.call_tool("get_pr_diff", {
            "pr_url": "https://github.com/owner/repo/pull/123",
            "api_key": "sk_live_1234567890abcdef"
        })
        print(result)
```

### Using Claude Desktop Configuration

For Claude Desktop, configure the MCP server in your `claude_desktop_config.json`:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "prdiffer-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/PRDiffer",
        "run",
        "python",
        "prdiffer/server.py"
      ],
      "env": {
        "MCP_AUTH_ENABLED": "true",
        "MCP_API_KEYS": "sk_live_1234567890abcdef",
        "GITHUB_TOKEN": "ghp_your_github_token_here"
      }
    }
  }
}
```

### Using cURL for Testing

You can test authentication using cURL:

```bash
# Test without authentication (will fail if auth is enabled)
curl -X POST http://127.0.0.1:9102/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "get_pr_diff",
      "arguments": {
        "pr_url": "https://github.com/owner/repo/pull/123"
      }
    }
  }'
```

### Using Insomnia or Postman

For API testing tools:

1. Set method to `POST`
2. Set URL to `http://127.0.0.1:9102/mcp`
3. Set Content-Type to `application/json`
4. Set body to:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_pr_diff",
    "arguments": {
      "pr_url": "https://github.com/owner/repo/pull/123",
      "api_key": "sk_live_1234567890abcdef"
    }
  }
}
```

### Python MCP Client Example

```python
import asyncio
from mcp import Client

async def fetch_pr_diff_with_auth():
    """Fetch PR diff with authentication."""

    # Server configuration
    server_url = "http://127.0.0.1:9102/mcp"
    api_key = "sk_live_1234567890abcdef"
    pr_url = "https://github.com/owner/repo/pull/123"

    async with Client(server_url) as client:
        # Option 1: Pass API key directly in tool call
        result = await client.call_tool("get_pr_diff", {
            "pr_url": pr_url,
            "api_key": api_key
        })

        # Option 2: Set API key as header (for custom client implementations)
        # headers = {"X-API-Key": api_key}
        # result = await client.call_tool("get_pr_diff", {
        #     "pr_url": pr_url
        # }, headers=headers)

        return result

# Run the example
asyncio.run(fetch_pr_diff_with_auth())
```

### TypeScript/JavaScript MCP Client Example

```typescript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

async function fetchPRDiffWithAuth() {
    // Create client with stdio transport
    const transport = new StdioClientTransport({
        command: "uv",
        args: [
            "--directory",
            "/path/to/PRDiffer",
            "run",
            "python",
            "prdiffer/server.py"
        ],
        env: {
            MCP_AUTH_ENABLED: "true",
            MCP_API_KEYS: "sk_live_1234567890abcdef",
            GITHUB_TOKEN: "ghp_your_github_token_here"
        }
    });

    const client = new Client({
        name: "prdiffer-client",
        version: "1.0.0"
    }, {
        capabilities: {}
    });

    await client.connect(transport);

    // Call the tool
    const result = await client.callTool({
        name: "get_pr_diff",
        arguments: {
            pr_url: "https://github.com/owner/repo/pull/123",
            api_key: "sk_live_1234567890abcdef"
        }
    });

    console.log(result);
    await client.close();
}

fetchPRDiffWithAuth().catch(console.error);
```

---

## Security Best Practices

### Key Generation

Generate strong, random API keys:

```python
import secrets

def generate_api_key(prefix: str = "sk_live_") -> str:
    """Generate a secure random API key."""
    # Generate 32 random bytes (64 hex characters)
    random_bytes = secrets.token_hex(32)
    return f"{prefix}{random_bytes}"

# Generate a new key
api_key = generate_api_key()
print(f"Generated API key: {api_key}")

# Output: sk_live_9f8e7d6c5b4a3210fedcba9876543210abcdef1234567890fedcba
```

### Key Storage and Management

**DO:**
- Store API keys in environment variables
- Use `.env` files (add to `.gitignore`)
- Rotate keys regularly
- Use different keys for different environments
- Monitor key usage

**DON'T:**
- Commit API keys to version control
- Share API keys in chat/email
- Use default or predictable keys
- Store keys in plain text configuration files

### Example Environment-Specific Configuration

```bash
# Development
MCP_AUTH_ENABLED=false
MCP_API_KEYS=dev_key_for_testing

# Staging
MCP_AUTH_ENABLED=true
MCP_API_KEYS=staging_key_abc123,staging_key_def456

# Production
MCP_AUTH_ENABLED=true
MCP_API_KEYS=prod_key_unique1,prod_key_unique2,prod_key_unique3
MCP_ADMIN_API_KEY=admin_key_production_secret
```

### Rate Limiting per Client

Each API key has independent rate limiting:

```python
# Rate limit configuration in settings.toml
[default]
github.rate_limit = 5000  # Requests per hour per client
```

If you need different rate limits for different clients, you can:
1. Issue separate API keys to each client
2. Configure rate limits per key in the `RateLimiter` component

### Key Rotation Strategy

1. **Generate New Keys**: Create new API keys
2. **Update Configuration**: Add new keys to `MCP_API_KEYS`
3. **Deploy Changes**: Restart the MCP server
4. **Update Clients**: Notify clients to update their keys
5. **Remove Old Keys**: After clients are updated, remove old keys from configuration

Example rotation process:
```bash
# Step 1: Add new keys alongside existing keys
export MCP_API_KEYS="old_key_abc123,new_key_def456"

# Step 2: Deploy and verify clients work with new keys

# Step 3: Remove old key
export MCP_API_KEYS="new_key_def456"
```

---

## Troubleshooting

### Authentication Errors

**Error: "Authentication failed. Please provide a valid API key via the 'api_key' parameter."**

**Causes:**
1. Authentication is enabled but no API key was provided
2. The provided API key is invalid

**Solutions:**
1. Check that `MCP_AUTH_ENABLED=true` is set
2. Verify the API key is included in the request
3. Confirm the API key matches one in `MCP_API_KEYS`

**Example Fix:**
```python
# Wrong (missing API key)
result = await client.call_tool("get_pr_diff", {
    "pr_url": "https://github.com/owner/repo/pull/123"
})

# Correct (with API key)
result = await client.call_tool("get_pr_diff", {
    "pr_url": "https://github.com/owner/repo/pull/123",
    "api_key": "sk_live_1234567890abcdef"
})
```

### Rate Limiting Errors

**Error: "Rate limit exceeded. Please try again later."**

**Causes:**
1. Client has exceeded the rate limit
2. Multiple clients sharing the same API key

**Solutions:**
1. Wait for the rate limit window to reset
2. Use a dedicated API key per client
3. Increase the rate limit in settings.toml

### Configuration Not Loading

**Symptoms:**
- Authentication appears disabled despite `MCP_AUTH_ENABLED=true`
- API keys from `.env` not being recognized

**Solutions:**
1. Verify `.env` file exists in the project root
2. Check that `load_dotenv()` is called before server initialization
3. Ensure no typos in environment variable names
4. Restart the server after changing `.env`

### Testing Authentication Status

You can check the authentication status using the health endpoint:

```python
from mcp import Client

async def check_auth_status():
    async with Client("http://127.0.0.1:9102/mcp") as client:
        # Call health endpoint
        result = await client.call_tool("health", {})
        print(result)
        # Returns: {"status": "healthy", "authentication": {"enabled": true, ...}}

asyncio.run(check_auth_status())
```

### Verifying Key Configuration

Check what keys are loaded by examining the server startup logs:

```bash
# Run the server and check logs
uv run python prdiffer/server.py

# Look for messages like:
# [INFO] Authentication: Enabled
# [INFO] API Keys configured: 3 regular keys, 1 admin key
```

---

## Quick Reference

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `MCP_AUTH_ENABLED` | Enable/disable authentication | `true` or `false` |
| `MCP_API_KEYS` | Comma-separated list of valid API keys | `key1,key2,key3` |
| `MCP_ADMIN_API_KEY` | Admin API key (optional) | `admin_key_secret` |

### API Key Format

Recommended format: `sk_{environment}_{random_bytes}`

- `sk_live_` for production
- `sk_test_` for testing
- `sk_admin_` for admin keys

Example: `sk_live_9f8e7d6c5b4a3210fedcba9876543210abcdef1234567890fedcba`

### Common Commands

```bash
# Enable authentication
export MCP_AUTH_ENABLED=true

# Set API keys
export MCP_API_KEYS="key1,key2,key3"

# Run server with authentication
uv run python prdiffer/server.py

# Test with curl
curl -X POST http://127.0.0.1:9102/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_pr_diff","arguments":{"pr_url":"https://github.com/owner/repo/pull/123","api_key":"sk_live_123"}}}'
```

---

## Additional Resources

- **Main Documentation**: See `CLAUDE.md` for project overview
- **Configuration Reference**: See `settings.toml` for all configuration options
- **Security Implementation**: See `prdiffer/application/components/authentication.py`
- **Input Validation**: See `prdiffer/infrastructure/security/input_validator.py`

---

## Support

For issues or questions:
1. Check this guide's troubleshooting section
2. Review the logs for detailed error messages
3. Verify your configuration matches the examples
4. Ensure you're using a compatible MCP client version
