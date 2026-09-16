"""Build the Claude Desktop extension: dist/zhaw-moodle-<version>.mcpb

The bundle contains this project's source, pyproject.toml and uv.lock; Claude Desktop
creates the Python environment with uv on first start (manifest server type "uv").

Usage: uv run python scripts/build_extension.py   (needs Node.js for `npx @anthropic-ai/mcpb`)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "extension"
DIST = ROOT / "dist"
MCPB = "@anthropic-ai/mcpb@2"

COPY = ["pyproject.toml", "uv.lock", "README.md", "LICENSE"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


async def server_tools() -> list[dict[str, str]]:
    from zhaw_moodle_mcp.config import Config, SyncConfig
    from zhaw_moodle_mcp.server import create_server

    # the server opens its database right away; on Windows the file stays locked until exit
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        server = create_server(Config(sync=SyncConfig(database=Path(tmp) / "m.db")))
        tools = await server.list_tools()
    return [{"name": t.name, "description": (t.description or "").strip().splitlines()[0]} for t in tools]


def manifest(version: str, tools: list[dict[str, str]]) -> dict:
    data = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    data["version"] = version
    data["tools"] = tools
    return data


def npx(*args: str) -> None:
    command = ["npx", "-y", MCPB, *args]
    subprocess.run(command, check=True, shell=sys.platform == "win32")


def main() -> int:
    version = project_version()
    tools = asyncio.run(server_tools())
    DIST.mkdir(exist_ok=True)
    output = DIST / f"zhaw-moodle-{version}.mcpb"

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "zhaw-moodle"
        bundle.mkdir()
        for name in COPY:
            shutil.copy2(ROOT / name, bundle / name)
        shutil.copytree(ROOT / "src", bundle / "src", ignore=IGNORE)
        shutil.copy2(EXTENSION / "icon.png", bundle / "icon.png")
        shutil.copy2(EXTENSION / ".mcpbignore", bundle / ".mcpbignore")
        (bundle / "manifest.json").write_text(
            json.dumps(manifest(version, tools), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        npx("validate", str(bundle / "manifest.json"))
        output.unlink(missing_ok=True)
        npx("pack", str(bundle), str(output))

    print(f"\nBuilt {output} ({output.stat().st_size // 1024} KB, {len(tools)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
