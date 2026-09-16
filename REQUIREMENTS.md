# ZHAW Moodle MCP Server

## 1. Purpose

The goal of the project is to build an MCP server that allows an AI client such as Claude Code to interact with the user's ZHAW Moodle account.

The system should primarily automate access to learning materials while keeping authentication simple and secure.

The user should not need to manually copy Moodle cookies, API tokens, session IDs, or passwords.

The expected user experience is:

1. Start the MCP server.
2. If no valid Moodle session exists, the system opens a browser.
3. The browser navigates to the ZHAW Moodle login page.
4. The user logs in manually using SWITCH edu-ID.
5. After successful authentication, the system stores the authenticated browser session locally.
6. The browser can be closed.
7. The MCP server can access Moodle using the authenticated session.
8. Future requests reuse the stored session until it expires.
9. If the session expires, the login browser is opened again automatically.

---

# 2. Project Goals

The MCP server must allow an AI agent to:

- authenticate to ZHAW Moodle through the user's normal browser login flow;
- discover the user's Moodle courses;
- inspect course structure;
- list learning materials;
- find files and resources;
- download PDFs and other course files;
- detect newly added learning materials;
- inspect assignments and deadlines;
- inspect Moodle quizzes;
- interact with explicitly allowed practice/self-test quizzes;
- expose Moodle functionality through clean MCP tools.

The MCP server should act as a reusable abstraction over Moodle so that the AI does not need to understand Moodle HTML directly.

---

# 3. Non-Goals

The first version does not need to:

- store the user's SWITCH edu-ID password;
- bypass MFA;
- bypass SWITCH edu-ID authentication;
- bypass Moodle permissions;
- modify course content;
- impersonate lecturers or other users;
- manage other students;
- post messages or forum posts;
- modify grades;
- submit graded assignments automatically;
- automatically submit graded exams or graded quizzes.

---

# 4. Authentication

## 4.1 Authentication Method

Authentication must use the normal ZHAW Moodle web login flow.

The system must NOT require:

- Moodle Web Service API tokens;
- username/password configuration files;
- manual cookie copying;
- browser developer tools.

Authentication should be based on an authenticated browser session and cookies.

Recommended implementation:

- Playwright;
- Chromium;
- persistent browser context or exported storage state.

---

# 4.2 Initial Login Flow

If no valid session exists:

1. MCP detects that authentication is required.
2. MCP launches a visible browser window.
3. Browser opens the configured ZHAW Moodle URL.
4. User selects/login through SWITCH edu-ID normally.
5. User completes all authentication steps manually.
6. User completes MFA if required.
7. MCP waits until Moodle confirms successful authentication.
8. MCP extracts/stores the authenticated browser state.
9. MCP verifies that a Moodle-authenticated page can be accessed.
10. Login flow finishes.

The MCP server must never ask the AI model for the user's SWITCH edu-ID password.

---

# 4.3 Session Persistence

Authentication state must survive MCP server restarts.

Example:

```text
~/.config/zhaw-moodle-mcp/
    auth/
        storage_state.json
```

The stored session may include:

- cookies;
- browser local storage;
- browser session metadata required by Moodle.

The MCP server should preferably use Playwright `storage_state`.

Example:

```python
context.storage_state(path="storage_state.json")
```

---

# 4.4 Session Validation

Before performing authenticated Moodle operations, the server should validate the current session.

Example validation:

```text
GET Moodle dashboard

authenticated:
    continue

redirected to login:
    session expired
```

If the session has expired:

```text
MCP request
    ↓
check session
    ↓
session invalid
    ↓
open browser
    ↓
user logs in through SWITCH edu-ID
    ↓
save new session
    ↓
retry original operation
```

The user should not have to restart the MCP server manually.

---

# 4.5 Logout

Provide:

```text
moodle_logout()
```

Logout must:

- clear saved cookies;
- remove stored browser authentication state;
- invalidate the local Moodle session;
- require authentication during the next Moodle request.

---

# 5. Security Requirements

Authentication data must never be:

- committed to Git;
- returned through MCP tool responses;
- printed into logs;
- included in exception messages;
- exposed to the AI model.

Files such as:

```text
storage_state.json
cookies.json
```

must be included in `.gitignore`.

Recommended location:

```text
~/.config/zhaw-moodle-mcp/
```

instead of the Git repository.

On supported operating systems, sensitive credentials/session data should use restricted file permissions.

Example on Unix:

```text
chmod 600 storage_state.json
```

Debug logging must redact:

- cookies;
- authorization headers;
- session IDs;
- CSRF tokens;
- SSO parameters.

---

# 6. Moodle Client Architecture

The MCP implementation should separate the MCP interface from the Moodle implementation.

Recommended abstraction:

```text
MCP Tools

        ↓

MoodleService

        ↓

MoodleClient

        ↓

AuthenticatedBrowserSession

        ↓

ZHAW Moodle
```

The MCP tools must not directly contain Playwright-specific logic.

Example:

```python
class MoodleClient:
    async def get_courses(self): ...
    async def get_course(self, course_id): ...
    async def get_course_resources(self, course_id): ...
    async def download_resource(self, resource_id): ...
```

This allows the authentication/backend implementation to be replaced later.

---

# 7. Course Discovery

Provide MCP tool:

```text
moodle_list_courses()
```

It should return the courses available to the current user.

Example:

```json
[
    {
        "id": "1234",
        "name": "Programmieren 1",
        "short_name": "PROG1",
        "url": "...",
        "status": "active"
    },
    {
        "id": "5678",
        "name": "Wirtschaftsrecht",
        "short_name": "WIR",
        "url": "...",
        "status": "active"
    }
]
```

Where possible, courses from old semesters should be distinguishable from current courses.

---

# 8. Course Structure

Provide:

```text
moodle_get_course(course_id)
```

The result should expose the course hierarchy.

Example:

```text
Programmieren 1

Week 1
 ├── Introduction.pdf
 ├── Exercise 01.pdf
 └── Moodle Quiz

Week 2
 ├── Loops.pdf
 ├── Exercise 02.pdf
 └── Assignment

Week 3
 └── OOP.pdf
```

Resources should include stable identifiers whenever possible.

---

# 9. Resource Discovery

Provide:

```text
moodle_list_resources(course_id)
```

Supported resource types should include at least:

- PDF;
- PowerPoint;
- Word;
- Excel;
- ZIP;
- plain files;
- Moodle pages;
- external links;
- folders.

Example output:

```json
{
    "id": "...",
    "course_id": "...",
    "section": "Week 3",
    "name": "OOP Introduction.pdf",
    "type": "pdf",
    "url": "...",
    "size": 2134452,
    "modified_at": "...",
    "downloadable": true
}
```

---

# 10. File Downloading

Provide:

```text
moodle_download_resource(resource_id)
```

Optional parameter:

```text
destination
```

Example:

```text
moodle_download_resource(
    resource_id="abc",
    destination="./ZHAW/Programming/"
)
```

Downloaded files should preserve their original filename whenever possible.

---

# 11. Local File Organization

Default download directory:

```text
~/ZHAW/
```

Recommended structure:

```text
ZHAW/
├── Programmieren/
│   ├── Week_01/
│   ├── Week_02/
│   └── Week_03/
│
├── Wirtschaftsrecht/
│
└── Socio_Technical_Skills/
```

Folder names should be sanitized for the host operating system.

---

# 12. Moodle Search

Provide:

```text
moodle_search(query)
```

Possible searches:

```text
moodle_search("OR ZGB")
moodle_search("Loops")
moodle_search("Semesterprogramm")
```

Search should look through:

- course names;
- section names;
- resource names;
- Moodle page titles;
- assignment names;
- quiz names.

Example natural language usage:

```text
Find the PDF about OR and ZGB from Wirtschaftsrecht.
```

---

# 13. Course Synchronization

Provide:

```text
moodle_sync_course(course_id)
```

The tool should:

1. retrieve current Moodle resources;
2. compare them with the locally known resources;
3. identify new files;
4. download new resources;
5. optionally update changed resources;
6. return a sync summary.

Example:

```text
Course: Wirtschaftsrecht

New:
+ OR_Einfuehrung.pdf
+ ZGB_Uebungen.pdf

Updated:
~ Semesterplan.pdf

Unchanged:
14 files
```

---

# 14. Global Synchronization

Provide:

```text
moodle_sync_all()
```

This should synchronize all active courses.

Example:

```text
3 courses checked

Programming
2 new files

Wirtschaftsrecht
1 new file

Socio Technical Skills
No changes
```

---

# 15. Local Metadata

The application should maintain local resource metadata.

Example:

```text
~/.local/share/zhaw-moodle-mcp/
    metadata.db
```

SQLite is recommended.

Possible tables:

```text
courses
resources
downloads
sync_history
```

Resource metadata should allow the server to detect:

- new resources;
- renamed resources;
- modified resources;
- already downloaded resources.

---

# 16. Assignments

Provide:

```text
moodle_list_assignments(course_id=None)
```

Return:

- assignment name;
- course;
- description;
- opening date;
- deadline;
- submission status;
- URL.

Example:

```json
{
    "course": "Programmieren",
    "assignment": "Exercise 03",
    "deadline": "2026-09-25T23:59:00+02:00",
    "submitted": false
}
```

---

# 17. Upcoming Deadlines

Provide:

```text
moodle_get_deadlines()
```

The AI should be able to ask:

```text
What do I need to submit this week?
```

The MCP tool should return structured deadline information rather than requiring the AI to scrape the Moodle dashboard itself.

---

# 18. Quiz Discovery

Provide:

```text
moodle_list_quizzes(course_id)
```

Return:

- quiz ID;
- quiz name;
- course;
- availability;
- attempt status;
- number of attempts;
- maximum attempts if visible;
- grading information if visible;
- URL.

---

# 19. Quiz Details

Provide:

```text
moodle_get_quiz(quiz_id)
```

The MCP server should identify whether the quiz appears to be:

- practice/self-test;
- graded;
- unknown.

When classification is uncertain, return:

```text
quiz_type = unknown
```

instead of guessing.

---

# 20. Practice Quiz Interaction

For practice/self-test quizzes, the MCP server may expose tools such as:

```text
moodle_start_quiz(quiz_id)

moodle_get_quiz_questions(attempt_id)

moodle_answer_quiz_question(
    attempt_id,
    question_id,
    answer
)

moodle_next_quiz_page(attempt_id)

moodle_get_quiz_result(attempt_id)
```

The AI can therefore:

1. start a permitted practice quiz;
2. read the question;
3. reason about the answer;
4. select an answer;
5. continue through the quiz;
6. inspect feedback/results.

---

# 21. Quiz Submission Safety

Submission should be handled separately from answering questions.

Recommended design:

```text
moodle_answer_quiz_question()
```

and:

```text
moodle_submit_quiz()
```

must be separate MCP tools.

For graded or unknown quizzes, final submission must not happen automatically.

Before final submission, the server should require explicit user confirmation.

Example:

```text
Quiz filled:
18/20 questions answered.

Submission has NOT been performed.
```

A configuration option should allow final submission functionality to be completely disabled.

Example:

```toml
[quiz]
allow_submit = false
```

Default:

```text
false
```

---

# 22. Announcements

Provide:

```text
moodle_get_announcements(course_id=None)
```

Return recent course announcements if accessible.

Useful AI request:

```text
What changed in Moodle since Monday?
```

---

# 23. Recent Changes

Provide:

```text
moodle_get_recent_changes(
    since="..."
)
```

It should aggregate:

- new files;
- updated files;
- assignments;
- deadlines;
- announcements;
- quizzes.

Example response:

```text
Since 2026-09-14:

Programmieren
+ Week 2 Exercise.pdf
+ Quiz 2

Wirtschaftsrecht
+ New assignment
Deadline: 2026-09-22

Socio Technical Skills
No changes
```

---

# 24. Recommended MCP Tools

Initial MCP tool set:

```text
moodle_auth_status

moodle_login

moodle_logout

moodle_list_courses

moodle_get_course

moodle_list_resources

moodle_search

moodle_download_resource

moodle_sync_course

moodle_sync_all

moodle_list_assignments

moodle_get_deadlines

moodle_get_announcements

moodle_get_recent_changes

moodle_list_quizzes

moodle_get_quiz

moodle_start_quiz

moodle_get_quiz_questions

moodle_answer_quiz_question

moodle_get_quiz_result
```

Optional:

```text
moodle_submit_quiz
```

It should be disabled by default.

---

# 25. MCP Tool Design

MCP tools should return structured JSON-friendly objects.

Bad:

```text
"Here are your Moodle courses..."
```

Preferred:

```json
{
    "courses": [
        {
            "id": "123",
            "name": "Programmieren"
        }
    ]
}
```

The AI client should handle natural-language presentation.

---

# 26. Browser Automation Requirements

Playwright is the preferred browser automation library.

Recommended:

```text
Python
FastMCP
Playwright
httpx
BeautifulSoup/lxml
Pydantic
SQLite
```

Playwright should primarily be responsible for:

- login;
- SSO redirects;
- session establishment;
- operations that cannot reliably be performed through HTTP requests.

Once authenticated, ordinary HTTP requests may reuse the browser cookies where practical.

Architecture:

```text
SWITCH edu-ID Login
       ↓
Playwright
       ↓
Authenticated cookies
       ↓
Moodle HTTP Client
       ↓
Moodle
```

This avoids opening a browser for every MCP request.

---

# 27. Browser Visibility

Login browser must run in headed mode:

```text
headless = false
```

because the user must interact with SWITCH edu-ID.

After authentication, normal Moodle operations should preferably run without visible browser windows.

---

# 28. Login Detection

The server must detect successful Moodle authentication automatically.

Possible indicators:

- Moodle dashboard becomes accessible;
- expected authenticated Moodle navigation appears;
- URL leaves the SSO/login flow;
- authenticated user data can be retrieved.

The user should not need to press:

```text
"Login finished"
```

in the terminal.

---

# 29. Error Handling

The server should expose meaningful errors.

Examples:

```text
AUTH_REQUIRED
SESSION_EXPIRED
COURSE_NOT_FOUND
RESOURCE_NOT_FOUND
DOWNLOAD_FAILED
QUIZ_NOT_AVAILABLE
QUIZ_ATTEMPT_NOT_ALLOWED
MOODLE_UNAVAILABLE
```

Avoid exposing raw HTML errors to the AI unless explicitly requested for debugging.

---

# 30. Retry Behaviour

Safe GET-like operations may be retried automatically.

Examples:

```text
list_courses
get_course
list_resources
download_resource
```

Potentially destructive or state-changing operations must not be automatically repeated unless their idempotency is known.

Examples:

```text
start_quiz
answer_quiz
submit_quiz
```

---

# 31. Configuration

Example:

```toml
[moodle]
base_url = "https://..."
download_directory = "~/ZHAW"

[browser]
browser = "chromium"
headless_after_login = true

[session]
storage_state = "~/.config/zhaw-moodle-mcp/storage_state.json"

[sync]
database = "~/.local/share/zhaw-moodle-mcp/moodle.db"

[quiz]
allow_practice_interaction = true
allow_submit = false
```

No password should exist in the configuration.

---

# 32. Logging

Application logs should contain:

- tool called;
- course/resource ID;
- operation duration;
- HTTP status where relevant;
- authentication state transitions;
- errors.

Example:

```text
INFO Checking Moodle authentication
INFO Moodle session valid
INFO Listing courses
INFO Found 7 courses
INFO Downloading resource 139201
INFO Download complete
```

Never log:

```text
MoodleSession
cookies
SWITCH credentials
CSRF token
authorization headers
```

---

# 33. Proposed Project Structure

```text
zhaw-moodle-mcp/
│
├── pyproject.toml
├── README.md
├── REQUIREMENTS.md
├── .gitignore
│
├── src/
│   └── zhaw_moodle_mcp/
│       │
│       ├── server.py
│       │
│       ├── config.py
│       │
│       ├── models/
│       │   ├── course.py
│       │   ├── resource.py
│       │   ├── assignment.py
│       │   └── quiz.py
│       │
│       ├── auth/
│       │   ├── session.py
│       │   └── browser.py
│       │
│       ├── moodle/
│       │   ├── client.py
│       │   ├── courses.py
│       │   ├── resources.py
│       │   ├── assignments.py
│       │   ├── quizzes.py
│       │   └── parser.py
│       │
│       ├── sync/
│       │   ├── database.py
│       │   └── sync_service.py
│       │
│       └── tools/
│           ├── auth.py
│           ├── courses.py
│           ├── resources.py
│           ├── assignments.py
│           ├── quizzes.py
│           └── sync.py
│
└── tests/
```

---

# 34. Main User Stories

## Authentication

As a user, I want the Moodle MCP server to open the normal Moodle login page so that I can authenticate using SWITCH edu-ID without providing my credentials to the application.

### Acceptance Criteria

- login browser opens automatically;
- normal SWITCH edu-ID flow is used;
- MFA works normally;
- password is never stored;
- successful login is detected automatically;
- authenticated session is stored;
- future MCP calls reuse the session.

---

## Session Reauthentication

As a user, I want the application to automatically detect an expired Moodle session so that I do not need to troubleshoot authentication manually.

### Acceptance Criteria

- expired sessions are detected;
- login window opens automatically;
- user logs in;
- original MCP operation continues afterward.

---

## Course Discovery

As a user, I want the AI to see my Moodle courses so that I can ask questions about my current studies.

### Acceptance Criteria

The MCP server returns structured information for every accessible Moodle course.

---

## Learning Materials

As a user, I want the AI to find and download Moodle learning materials so that I do not need to manually navigate through every course.

### Acceptance Criteria

The AI can:

- list sections;
- list resources;
- identify PDFs;
- download selected resources;
- return the local file path.

---

## Course Synchronization

As a user, I want the AI to synchronize Moodle course materials so that new documents automatically appear in my local ZHAW directory.

### Acceptance Criteria

Synchronization identifies:

- new files;
- changed files;
- already synchronized files.

---

## Deadlines

As a user, I want the AI to see Moodle assignments and deadlines so that I can ask what I need to complete next.

### Acceptance Criteria

The MCP returns structured assignment/deadline data across courses.

---

## Practice Tests

As a user, I want the AI to interact with Moodle practice tests so that it can help me learn and identify topics that I do not understand.

### Acceptance Criteria

For allowed practice tests, the AI can:

- open the quiz;
- retrieve questions;
- fill answers;
- navigate through questions;
- retrieve feedback/results.

Final submission is handled separately.

---

# 35. MVP

Version `0.1` should contain only:

```text
Authentication
↓
list courses
↓
list course sections/resources
↓
download files
↓
sync files
```

Required MCP tools:

```text
moodle_auth_status
moodle_login
moodle_logout

moodle_list_courses
moodle_get_course
moodle_list_resources
moodle_download_resource

moodle_sync_course
```

This version is enough to validate that cookie-based ZHAW Moodle access works reliably.

---

# 36. Version 0.2

Add:

```text
search
assignments
deadlines
announcements
recent changes
```

---

# 37. Version 0.3

Add:

```text
quiz discovery
practice quiz reading
practice quiz interaction
quiz feedback
```

---

# 38. Future Vision

The final system should allow interactions such as:

```text
"Check Moodle and tell me what is new."

"Download everything added this week."

"Synchronize all my current courses."

"Find the PDF from today's Wirtschaftsrecht lecture."

"What assignments are due in the next seven days?"

"Download all PDFs from Programmieren."

"Find everything related to OOP."

"Open the practice quiz from Programming and help me work through it."
```

The AI should interact only with the MCP abstraction and should not need to know how ZHAW Moodle, SWITCH edu-ID, cookies, Playwright, or Moodle HTML work internally.