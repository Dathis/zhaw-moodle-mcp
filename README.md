# ZHAW Moodle MCP

[![PyPI](https://img.shields.io/pypi/v/zhaw-moodle-mcp)](https://pypi.org/project/zhaw-moodle-mcp/)
[![CI](https://github.com/Dathis/zhaw-moodle-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Dathis/zhaw-moodle-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/Dathis/zhaw-moodle-mcp/blob/main/LICENSE)

Use your ZHAW Moodle from Claude: courses, materials, downloads, deadlines and announcements.
You log in yourself with SWITCH edu-ID — your password is never seen or stored, and everything stays
on your computer.

> Unofficial student project, not affiliated with ZHAW. Course material is for personal study only.

## Quick start

### Claude Desktop (recommended)

1. Download `zhaw-moodle-<version>.mcpb` from the
   [latest release](https://github.com/Dathis/zhaw-moodle-mcp/releases/latest).
2. Double-click it, or in Claude Desktop open **Settings → Extensions → Advanced settings →
   Install Extension…** and pick the file. Click **Install**.
3. Open a new chat and ask *"Which Moodle courses do I have?"*. The first start takes a few
   seconds; then a browser opens for the SWITCH edu-ID login.

No config files to edit. In the chat's **+ → prompts** menu you find ready-made study
sessions: study assistant, last lecture summary, deadlines overview, what's new, exam preparation.

### Claude Code

Install [uv](https://docs.astral.sh/uv/), then:

```bash
claude mcp add zhaw-moodle -- uvx zhaw-moodle-mcp@latest
```

### Ask Claude

*"Summarise the last lecture of Software Engineering 1"*, *"Show all my deadlines as a table"*,
*"Sync all my courses"*, *"What's new since Monday?"*. Files go to the `ZHAW` folder in your home
directory.

## Good to know

- **Browser:** login uses your default browser if it is Chrome, Edge, Brave or Vivaldi, otherwise
  another installed one (Firefox and Safari are not supported).
- **Updates:** the extension is updated by installing a newer `.mcpb`; with `uvx ...@latest`
  (Claude Code) updates install automatically when Claude starts.
- **Extension does not start:** it runs with [uv](https://docs.astral.sh/uv/). If Claude Desktop
  reports that `uv` is missing, install uv and restart Claude completely (also from the tray).
- Sync never deletes local files. Logging out (ask Claude, or `uvx zhaw-moodle-mcp logout`) removes the
  stored session and the login browser profile; a running server notices it.
- Session and settings live in `~/.zhaw-moodle-mcp` on Windows (outside AppData, so Claude from the
  Microsoft Store and the command line share them) and in the usual app folders on macOS/Linux.

## Configuration

Optional. The extension asks for the download folder and browser when you install it. Otherwise
run `uvx zhaw-moodle-mcp config-path` to see where `config.toml` goes:

```toml
[moodle]
download_directory = "~/ZHAW"

[browser]
name = "auto"  # or "chrome", "msedge", "brave", "vivaldi"
# executable_path = "C:/path/to/browser.exe"  # any other Chromium-based browser
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

In this folder Claude Code picks up the server from `.mcp.json`, plus a
[study assistant](https://github.com/Dathis/zhaw-moodle-mcp/blob/main/.claude/agents/study-assistant.md) subagent (copy it to `~/.claude/agents/` to use it
everywhere). Build the Claude Desktop extension with `uv run python scripts/build_extension.py` (needs Node.js).
Pushing a `vX.Y.Z` tag that matches `pyproject.toml` publishes to PyPI and attaches the `.mcpb`
to a GitHub release.
