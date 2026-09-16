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

## Learned during v0.1 implementation

- Some course files are HTML themselves (e.g. `*_player.html`, `text/html`) → a `text/html`
  response from `pluginfile.php` is **not** a sign of an expired session; only redirects are.
- Apache compresses GET responses and appends `-gzip` to the ETag, HEAD responses have the plain
  ETag → ETags must be normalised before comparing.
- `pluginfile.php/<ctx>/mod_resource/content/<revision>/<filename>` ignores the revision; after a
  file is replaced under a new name the old URL is 404 → re-resolve via `view.php?redirect=1`.
- `view.php` counts as an activity view, so sync resolves each file URL once, stores it and uses
  HEAD on `pluginfile.php` afterwards.
- A full sync of a course with 37 files takes ~12 s when nothing changed.

## Open questions

- Idle timeout of `MoodleSession`.
- Does re-login with persistent profile complete without user interaction (SSO still valid)?
- `mod/url` external target resolution; `mod/page` content handling.

---

# Spike v0.2 — assignments, deadlines, announcements, search, recent changes (2026-09-16)

Script: `spike/probe_v02.py`.

## AJAX availability

| Function | Result | Use |
|---|---|---|
| `core_calendar_get_action_events_by_timesort` | OK | deadlines: only open to-dos (assign `due`, quiz `close`), `action.name/actionable`, `overdue`, `timesort` |
| `core_calendar_get_calendar_monthly_view` | OK | all events of a month incl. quiz `open` |
| `core_calendar_get_calendar_upcoming_view` | OK | upcoming events (user lookahead) |
| `core_calendar_get_action_events_by_courses` | OK | — |
| `core_course_get_updates_since` | OK | per module: `configuration` / `contentfiles` + `timeupdated` → recent changes |
| `core_course_check_updates` | OK | — |
| `mod_forum_get_discussion_posts` | OK | announcement body, author, attachments |
| `message_popup_get_popup_notifications` | OK | (notifications: quiz opens, new forum posts) |
| `block_recentlyaccesseditems_get_recent_items` | OK | — |
| `mod_assign_get_assignments`, `mod_assign_get_submission_status` | `servicenotavailable` | → HTML |
| `mod_forum_get_forums_by_courses`, `mod_forum_get_forum_discussions` | `servicenotavailable` | → HTML |
| `core_search_get_results`, `core_calendar_get_calendar_events`, `core_course_get_course_module` | `servicenotavailable` | → HTML |

## HTML sources

- **Assignment** `mod/assign/view.php?id=<cmid>`:
  - dates: `[data-region=activity-dates] > div` = `<strong>Fällig:</strong> Sonntag, 8. November 2026, 23:59` (localised text only)
  - status: `table.generaltable`, rows label/value (German). Language-independent hints are td classes:
    `submissionstatus<status>` (absent when nothing was submitted), `submissiongraded` / `submissionnotgraded`,
    `timeremaining` / `overdue` / `earlysubmission` / `latesubmission`.
  - viewing the page logs a module view (completion for assignments is usually "submit", so harmless).
- **Announcements**: the news forum has `<body class="… forumtype-news …">` (named "Ankündigungen").
  `mod/forum/view.php?id=<cmid>` lists discussions as `tr[data-region=discussion-list-item][data-discussionid]`
  with `td.topic`, `td.author`, `time#time-created-<id>[data-timestamp]`, `time#time-modified-<id>[data-timestamp]`.
  Empty forum: no rows.
- **Global search** `search/index.php?q=…` works (Moodle global search is enabled): `.result` →
  `h4.result-title a` (URL with `#module-<cmid>` or module view URL), icon URL contains the module type,
  `.result-content` snippet, `.result-context-info` course link. Finds label/text content too.
- `course/recent.php` exists but is a large, section-grouped page; `core_course_get_updates_since` is simpler.
