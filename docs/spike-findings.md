# Spike findings — ZHAW Moodle access (2026-09-16)

Scripts: `spike/login.py`, `spike/probe.py`. Moodle: `https://moodle.zhaw.ch`, theme `boost_union`, Moodle 4.5+ (has `mod_subsection`).

## Authentication

- `/login/index.php` redirects straight to SWITCH edu-ID (`login.eduid.ch`) — no IdP selection page.
- **Anonymous visitors are auto-logged in as guest**: `M.cfg.userId == 1`, no `notloggedin` body class, `/my/` redirects to `/`.
  Login detection must use: `M.cfg.userId > 1` **and** no `a[href$="/login/index.php"]` on the page.
- Moodle needs only `MoodleSession` (session cookie, no expiry) — plus `_shibsession_*` and load-balancer cookie `BIGipServermoodle_443`.
  Server-side idle timeout is still unknown → measure.
- Persistent browser profile keeps edu-ID cookies (`ch_switch_aai_idp_otp_remember_browser`, ~1 year) → re-login should skip MFA.
- Playwright `channel="chrome"` (installed Chrome) works; no Chromium download needed.

## Data sources (httpx + session cookies)

AJAX endpoint `POST /lib/ajax/service.php?sesskey=…` (sesskey from `"sesskey":"…"` in any page):

| Function | Result | Use |
|---|---|---|
| `core_course_get_enrolled_courses_by_timeline_classification` | OK | course list; `classification` = inprogress/past/future/all |
| `core_courseformat_get_state` | OK (returns JSON **string**) | sections + course modules |
| `core_calendar_get_action_events_by_timesort` | OK | deadlines (v0.2) |
| `core_course_get_contents` | `servicenotavailable` | — (would have given file metadata) |
| `core_webservice_get_site_info` | `servicenotavailable` | — |

`core_courseformat_get_state`:
- `section[]`: `id, number, title, rawtitle, cmlist, component, itemid, parentsectionid, …`; titles are HTML-escaped (`&amp;`) → `html.unescape`.
- Subsections: section with `component == "mod_subsection"`; the parent's cm has `module == "subsection"`, `hasdelegatedsection == true`.
- `cm[]`: `id (cmid), name, module, url, sectionid, sectionnumber, uservisible, visible, …` — **no filename, size, mimetype or timemodified**.

Module types seen across 5 courses: resource, folder, url, page, label, forum, assign, quiz, lti, subsection, choicegroup.

## Files

- `GET /mod/resource/view.php?id=<cmid>&redirect=1` → follows to `pluginfile.php`, real file, `Content-Disposition` has original filename.
- `HEAD` on the same URL → `Last-Modified` + `ETag` (no `Content-Length`) → cheap change detection for sync.
- File type hint without download: activity icon in `course/view.php` (`img src=…/pdf?filtericon=1`).
- Folders: `/mod/folder/view.php?id=<cmid>` lists `pluginfile.php/…/mod_folder/content/…?forcedownload=1` links; ZIP download form (`download_folder.php`) also available.
- `mod/url`: `view.php?redirect=1` returns 303 — resolve target via HTML/`Location` (to verify).

## Implications for v0.1

1. Courses: timeline AJAX (gives current vs. past for free).
2. Structure: `core_courseformat_get_state`, resolve subsection nesting; HTML `course/view.php` only for type icons.
3. Resource ID = cmid; folder files = `cmid` + relative path.
4. Sync: HEAD → compare `ETag`/`Last-Modified` with SQLite; download only on change; store sha256.
5. Session validation: `GET /my/` + guest check; AJAX top-level error also signals expiry.

## Open questions

- Idle timeout of `MoodleSession`.
- Does re-login with persistent profile complete without user interaction (SSO still valid)?
- `mod/url` external target resolution; `mod/page` content handling.
