"""mcp-doctor: a checkup for MCP servers."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-doctor")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "unknown"
