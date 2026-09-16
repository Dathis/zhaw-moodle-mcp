# ZHAW Moodle MCP Server

MCP server that lets an AI client (e.g. Claude Code) work with your ZHAW Moodle
(`moodle.zhaw.ch`): list courses, inspect course structure, find and download
learning materials and keep a local copy in sync.

You log in yourself with SWITCH edu-ID in a normal browser window. The server never
sees or stores your password; it reuses the authenticated browser session.

Requirements and roadmap: [REQUIREMENTS.md](REQUIREMENTS.md) · Technical findings: [docs/spike-findings.md](docs/spike-findings.md)

## Status: v0.2

| Tool | Purpose |
|---|---|
| `moodle_auth_status` | Is there a valid Moodle session? |
| `moodle_login` | Open the browser for SWITCH edu-ID login (other tools do this automatically when needed) |
| `moodle_logout` | End the session and delete local cookies/browser profile |
| `moodle_list_courses` | Courses, marked `active` / `past` / `future` |
| `moodle_get_course` | Sections, subsections and all activities of a course |
| `moodle_list_resources` | Files (pdf, powerpoint, word, …), folders (incl. their files), links, pages |
| `moodle_download_resource` | Download a file or a whole folder, returns local paths |
| `moodle_sync_course` | Download new/changed files, report renamed/removed ones (`dry_run` supported) |
| `moodle_sync_all` | Sync all current courses, per-course summary |
| `moodle_search` | Find courses, sections, materials, assignments, quizzes by name (umlaut/typo tolerant); falls back to Moodle full-text search |
| `moodle_get_deadlines` | Open to-dos with deadlines (assignments, quizzes) across all courses |
| `moodle_list_assignments` | Assignments with dates, submission and grading status, description |
| `moodle_get_announcements` | Lecturer announcements with message text |
| `moodle_get_recent_changes` | New/updated materials, announcements and deadlines since a date (`2026-09-14`, `7d`) |

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Google Chrome (or Edge / Playwright Chromium, see configuration).

```bash
uv sync
```

Log in once (opens Chrome, finishes automatically after the SWITCH edu-ID login):

```bash
uv run zhaw-moodle-mcp login
```

Add the server to Claude Code:

```bash
claude mcp add zhaw-moodle -- uv --directory C:/path/to/zhaw-moodle-mcp run zhaw-moodle-mcp
```

Alternatively the repository contains a `.mcp.json`, so Claude Code opened in this folder offers the
server automatically.

Then ask things like *"Which Moodle courses do I have?"*, *"Sync Software Engineering 1"*,
*"What do I need to submit this week?"* or *"What is new in Moodle since Monday?"*.

### CLI

```text
zhaw-moodle-mcp              run the MCP server (stdio); also: python -m zhaw_moodle_mcp
zhaw-moodle-mcp login        log in via browser
zhaw-moodle-mcp status       check the stored session
zhaw-moodle-mcp logout       end session, delete local auth data
zhaw-moodle-mcp config-path  where the config file is expected
```

## How it works

```text
SWITCH edu-ID login in Chrome (Playwright, visible window)
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
- Reading assignments opens each assignment page, which Moodle logs as a view.

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
channel = "chrome"        # "chrome", "msedge" or "chromium" (needs: uv run playwright install chromium)
login_timeout = 300       # seconds to wait for the user to finish login
auto_login = true         # open the login window automatically when the session expired
# profile_directory = "<data dir>/browser-profile"

[session]
# storage_state = "<data dir>/auth/storage_state.json"
validation_ttl = 120      # seconds a successful session check is trusted

[sync]
# database = "<data dir>/moodle.db"

[search]
index_max_age_minutes = 60  # re-read current courses before searching if the index is older
```

`<data dir>` is `%LOCALAPPDATA%\zhaw-moodle-mcp` on Windows, `~/.local/share/zhaw-moodle-mcp` on Linux.

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
