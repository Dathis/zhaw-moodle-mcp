# ZHAW Moodle MCP Server

[![PyPI](https://img.shields.io/pypi/v/zhaw-moodle-mcp)](https://pypi.org/project/zhaw-moodle-mcp/)
[![CI](https://github.com/Dathis/zhaw-moodle-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Dathis/zhaw-moodle-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/Dathis/zhaw-moodle-mcp/blob/main/LICENSE)

MCP server that lets Claude (Claude Desktop, Claude Code or any other MCP client) work with
your ZHAW Moodle (`moodle.zhaw.ch`): list courses, find and download learning materials, keep
a local copy in sync, and see deadlines, assignments, announcements and what is new.

You log in yourself with SWITCH edu-ID in a normal browser window. The server never
sees or stores your password; it reuses the authenticated browser session, and everything
stays on your computer.

> Unofficial student project, not affiliated with or endorsed by ZHAW. Use it in line with
> the ZHAW ICT usage rules; downloaded course material is for your personal study only.

Requirements and roadmap: [REQUIREMENTS.md](https://github.com/Dathis/zhaw-moodle-mcp/blob/main/REQUIREMENTS.md) · Technical findings: [docs/spike-findings.md](https://github.com/Dathis/zhaw-moodle-mcp/blob/main/docs/spike-findings.md)

## Quick start

You need [Claude Desktop](https://claude.ai/download) or Claude Code and a Chromium-based
browser (Chrome, Edge, Brave or Vivaldi — on Windows Edge is always there).

**1. Install uv** (runs the server and brings its own Python):

- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

**2. Add the server to Claude**

*Claude Desktop:* Settings → Developer → Edit Config, paste this into `claude_desktop_config.json`
(merge it if the file already has `mcpServers`) and restart Claude Desktop completely:

```json
{
  "mcpServers": {
    "zhaw-moodle": {
      "command": "uvx",
      "args": ["zhaw-moodle-mcp@latest"]
    }
  }
}
```

*Claude Code:*

```bash
claude mcp add zhaw-moodle -- uvx zhaw-moodle-mcp@latest
```

**3. Use it:** ask *"Which Moodle courses do I have?"*. The first time, a browser window opens —
log in with SWITCH edu-ID there and the window closes by itself.
Files are downloaded to the `ZHAW` folder in your home directory.

More ideas: *"Sync all my courses"*, *"What do I need to submit this week?"*,
*"What is new in Moodle since Monday?"*, *"Summarise the slides of lecture 1 in Software Engineering"*.

Troubleshooting:

- *Claude Desktop can't find `uvx`:* quit Claude Desktop completely (also from the tray) and start it
  again. If that doesn't help, use the full path as `command`, e.g. `C:/Users/<you>/.local/bin/uvx.exe`.
- *No browser opens:* see `[browser]` in [Configuration](#configuration).
- Updates are installed automatically the next time Claude starts (`@latest`).

## Status: v0.3

| Tool | Purpose |
|---|---|
| `moodle_auth_status` | Is there a valid Moodle session? |
| `moodle_login` | Open the browser for SWITCH edu-ID login (other tools do this automatically when needed) |
| `moodle_logout` | End the session and delete local cookies/browser profile |
| `moodle_list_courses` | Courses, marked `active` / `past` / `future` |
| `moodle_get_course` | Sections, subsections and all activities of a course, incl. the text of text blocks |
| `moodle_get_content` | Text of a Moodle page or text block as Markdown, with its links |
| `moodle_list_resources` | Files (pdf, powerpoint, word, …), folders (incl. their files), links, pages |
| `moodle_download_resource` | Download a file, a whole folder or the files linked in a page/text block, returns local paths |
| `moodle_sync_course` | Download new/changed files, report renamed/removed ones (`dry_run` supported) |
| `moodle_sync_all` | Sync all current courses, per-course summary |
| `moodle_search` | Find courses, sections, materials, assignments, quizzes by name (umlaut/typo tolerant); falls back to Moodle full-text search |
| `moodle_get_deadlines` | Open to-dos with deadlines (assignments, quizzes) across all courses |
| `moodle_list_assignments` | Assignments with dates, submission and grading status, description |
| `moodle_get_announcements` | Lecturer announcements with message text |
| `moodle_get_recent_changes` | New/updated materials, announcements and deadlines since a date (`2026-09-14`, `7d`) |

## Development setup

Requires [uv](https://docs.astral.sh/uv/) and a Chromium-based browser (Chrome, Edge, Brave or Vivaldi).
The login opens in your default browser if it is one of these, otherwise in another installed one
(Firefox and Safari can't be automated; on Windows Edge is always available).

```bash
uv sync
```

Log in once (opens the browser, finishes automatically after the SWITCH edu-ID login):

```bash
uv run zhaw-moodle-mcp login
```

Add the server to Claude Code:

```bash
claude mcp add zhaw-moodle -- uv --directory C:/path/to/zhaw-moodle-mcp run zhaw-moodle-mcp
```

Alternatively the repository contains a `.mcp.json`, so Claude Code opened in this folder offers the
server automatically.

### Study assistant (Claude Code)

`.claude/agents/study-assistant.md` is a Claude Code subagent that uses this server as a tutor:
explaining lectures, guiding exercises without handing out solutions, exam preparation,
flashcards and deadline planning. It is available automatically when Claude Code runs in this
folder; to use it everywhere, copy the file to `~/.claude/agents/`.

### CLI

With the PyPI package, prefix the commands with `uvx` (e.g. `uvx zhaw-moodle-mcp login`).

```text
zhaw-moodle-mcp              run the MCP server (stdio); also: python -m zhaw_moodle_mcp
zhaw-moodle-mcp login        log in via browser
zhaw-moodle-mcp status       check the stored session
zhaw-moodle-mcp logout       end session, delete local auth data
zhaw-moodle-mcp config-path  where the config file is expected
```

## How it works

```text
SWITCH edu-ID login in a Chromium-based browser (Playwright, visible window)
        ↓  storage_state.json (cookies)
httpx client ── Moodle AJAX service (/lib/ajax/service.php, session + sesskey):
             │    courses, course structure, calendar to-dos, module updates, forum posts
             ── HTML pages: file types, folder contents, assignment status, announcement lists,
             │    Moodle full-text search
             ── pluginfile.php for downloads, HEAD + ETag for change detection
        ↓
SQLite metadata (course index for search/changes, known files, ETags, local paths, sync history)
```

- The browser is used only for login. A persistent browser profile keeps the edu-ID
  "remember this browser" state, so re-login usually needs no MFA.
- If the session expires during a request, the login window opens and the request is retried.
- Files are stored as `<download_directory>/<course>/<NN section>/<subsection>/<folder>/<file>`.
- Sync never deletes local files; files removed from Moodle are only reported.
- Search uses a local index of names that is refreshed from Moodle when older than an hour.
  Files inside folders become searchable once `moodle_list_resources` or a sync has seen them.
- Recent changes can only distinguish *new* from *updated* for courses the server already knew
  before the requested date; otherwise changes are reported as `changed`.
- Reading assignments or pages opens them in Moodle, which is logged as a view (and may mark a
  page as completed if the course tracks page views).
- Files linked inside pages or text blocks get a `resource_id` (`<activity id>/<file>`) for
  `moodle_download_resource`; the id of the page/block itself downloads all its linked files into
  `<course>/<section>/<page name>/`. These files are not part of `moodle_sync_course`.

## Configuration

Optional TOML file (see `zhaw-moodle-mcp config-path`, or set `ZHAW_MOODLE_MCP_CONFIG`).
All keys are optional; defaults shown:

```toml
[moodle]
base_url = "https://moodle.zhaw.ch"
download_directory = "~/ZHAW"
request_timeout = 60
max_concurrent_requests = 4
timezone = "Europe/Zurich" # how dates shown by Moodle are interpreted

[browser]
name = "auto"             # "auto", "chrome", "msedge", "brave", "vivaldi" or "chromium"
                          # ("chromium" needs: uvx --from zhaw-moodle-mcp playwright install chromium)
# executable_path = "C:/path/to/browser.exe"  # any other Chromium-based browser, overrides name
login_timeout = 300       # seconds to wait for the user to finish login
auto_login = true         # open the login window automatically when the session expired
# profile_directory = "<data dir>/browser-profile"  # one subfolder per browser

[session]
# storage_state = "<data dir>/auth/storage_state.json"
validation_ttl = 120      # seconds a successful session check is trusted

[sync]
# database = "<data dir>/moodle.db"

[search]
index_max_age_minutes = 60  # re-read current courses before searching if the index is older
```

`<data dir>` is `%LOCALAPPDATA%\zhaw-moodle-mcp` on Windows, `~/Library/Application Support/zhaw-moodle-mcp`
on macOS and `~/.local/share/zhaw-moodle-mcp` on Linux.

## Security

- No passwords, API tokens or manually copied cookies.
- Session data lives outside the repository and is restricted to the current user
  (`icacls` on Windows, `chmod 600/700` elsewhere).
- Tool results and error messages never contain cookies or session keys; logs go to
  stderr with cookies, sesskeys and SSO parameters redacted.
- `moodle_logout` removes the stored session **and** the browser profile.

## Development

```bash
uv run pytest
uv run ruff check src tests
```

Tests run against an in-memory fake Moodle (`tests/fake_moodle.py`), no network needed.
`spike/` contains the original exploration scripts; their output (`spike/output/`) is git-ignored
because it contains personal Moodle data.
