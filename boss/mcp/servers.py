from __future__ import annotations

from agents.mcp import MCPServerStdio

# Pinned MCP package versions for reproducible startup.
# Update these explicitly when upgrading; do not use @latest.
_APPLE_MCP_VERSION = "0.1.2"
_MCP_FILESYSTEM_VERSION = "0.6.2"
_MCP_MEMORY_VERSION = "0.6.2"


def create_apple_mcp() -> MCPServerStdio:
    return MCPServerStdio(
        name="apple",
        params={
            "command": "npx",
            "args": ["-y", f"apple-mcp@{_APPLE_MCP_VERSION}"],
        },
        cache_tools_list=True,
    )


def create_filesystem_mcp() -> MCPServerStdio:
    return MCPServerStdio(
        name="filesystem",
        params={
            "command": "npx",
            "args": [
                "-y",
                f"@modelcontextprotocol/server-filesystem@{_MCP_FILESYSTEM_VERSION}",
                "/Users/tj/Documents",
                "/Users/tj/Desktop",
                "/Users/tj/Downloads",
            ],
        },
        cache_tools_list=True,
    )


def create_memory_mcp() -> MCPServerStdio:
    return MCPServerStdio(
        name="memory",
        params={
            "command": "npx",
            "args": ["-y", f"@modelcontextprotocol/server-memory@{_MCP_MEMORY_VERSION}"],
        },
        cache_tools_list=True,
    )


def create_mcp_servers() -> dict[str, MCPServerStdio]:
    return {
        "apple": create_apple_mcp(),
        "filesystem": create_filesystem_mcp(),
        "memory": create_memory_mcp(),
    }
