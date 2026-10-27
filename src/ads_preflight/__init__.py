"""ads-preflight: preflight checks for ad-platform MCP servers."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ads-preflight")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "unknown"
