"""ZHAW Moodle MCP server."""

from importlib.metadata import version

__version__ = version("zhaw-moodle-mcp")


def main() -> None:
    from .cli import main as cli_main

    cli_main()
