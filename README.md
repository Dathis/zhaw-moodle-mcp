# ZHAW Moodle MCP

[![PyPI](https://img.shields.io/pypi/v/zhaw-moodle-mcp)](https://pypi.org/project/zhaw-moodle-mcp/)
[![CI](https://github.com/Dathis/zhaw-moodle-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Dathis/zhaw-moodle-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/Dathis/zhaw-moodle-mcp/blob/main/LICENSE)

Use your ZHAW Moodle from Claude: courses, materials, downloads, deadlines and announcements.
You log in yourself with SWITCH edu-ID — your password is never seen or stored, and everything stays
on your computer.

> Unofficial student project, not affiliated with ZHAW. Course material is for personal study only.

## Quick start

**1. Install [uv](https://docs.astral.sh/uv/)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

**2. Add it to Claude**

Claude Desktop → Settings → Developer → Edit Config, paste and restart Claude:

```json
{
  "mcpServers": {
    "zhaw-moodle": { "command": "uvx", "args": ["zhaw-moodle-mcp@latest"] }
  }
}
```

Claude Code: `claude mcp add zhaw-moodle -- uvx zhaw-moodle-mcp@latest`

**3. Ask Claude**

*"Which Moodle courses do I have?"* — the first time, a browser opens for the SWITCH edu-ID login.
Then try *"Sync all my courses"*, *"What is due this week?"* or *"What's new since Monday?"*.

Files go to the `ZHAW` folder in your home directory.

## Good to know

- **Browser:** login uses your default browser if it is Chrome, Edge, Brave or Vivaldi, otherwise
  another installed one (Firefox and Safari are not supported).
- **Updates** install automatically when Claude starts.
- **`uvx` not found in Claude Desktop:** quit Claude completely (also from the tray) and reopen it,
  or use the full path, e.g. `C:/Users/<you>/.local/bin/uvx.exe`.
- Sync never deletes local files. Logging out (`uvx zhaw-moodle-mcp logout`) removes the stored session.

## Configuration

Optional. Run `uvx zhaw-moodle-mcp config-path` to see where `config.toml` goes:

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
everywhere). Releases are published to PyPI by pushing a `vX.Y.Z` tag that matches `pyproject.toml`.
