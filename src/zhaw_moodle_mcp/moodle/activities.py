"""Assignments, calendar deadlines, announcements, module updates and Moodle's
global search. Parsers are pure functions; fetchers take a MoodleClient."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from ..models import ActivityDate, Attachment
from ..models.activity import GradingStatus, SubmissionStatus
from .client import MoodleClient
from .dates import from_timestamp, parse_moodle_date
from .parser import clean_text

# --- assignments ------------------------------------------------------------

_OPEN_WORDS = ("geöffnet", "öffnet", "opened", "opens", "ouvert", "aperto", "abgabebeginn", "allow submissions")
_DUE_WORDS = ("fällig", "due", "échéance", "scadenza")
_CUTOFF_WORDS = ("letzte abgabe", "abgabeschluss", "cut-off", "cutoff", "date limite", "termine ultimo")


@dataclass
class AssignmentPage:
    accessible: bool = True
    dates: list[ActivityDate] = field(default_factory=list)
    opens_at: datetime | None = None
    due_at: datetime | None = None
    cutoff_at: datetime | None = None
    submission_status: SubmissionStatus = "unknown"
    grading_status: GradingStatus = "unknown"
    overdue: bool | None = None
    time_remaining: str | None = None
    details: dict[str, str] = field(default_factory=dict)
    description: str | None = None


def _html_text(node: Tag) -> str:
    """Readable plain text with paragraph breaks."""
    for br in node.find_all("br"):
        br.replace_with("\n")
    blocks = []
    for el in node.find_all(["p", "li", "h1", "h2", "h3", "h4", "div"], recursive=True):
        if el.find(["p", "li", "div"]):
            continue  # only leaf blocks
        text = " ".join(el.get_text(" ").split())
        if text:
            blocks.append(("- " if el.name == "li" else "") + text)
    text = "\n".join(blocks) if blocks else node.get_text("\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_assignment_page(page_html: str, tz: ZoneInfo) -> AssignmentPage:
    soup = BeautifulSoup(page_html, "lxml")
    page = AssignmentPage()

    def own(el: Tag) -> bool:
        """Belongs to this activity, not to other activities listed on the page."""
        return el.find_parent("li", class_="activity") is None

    regions = [r for r in soup.select("[data-region='activity-dates']") if own(r)]
    for div in (d for r in regions[:1] for d in r.find_all("div", recursive=False)):
        label_el = div.find("strong")
        label = clean_text(label_el.get_text()).rstrip(":") if label_el else ""
        full = clean_text(div.get_text(" "))
        text = full[len(label) + 1:].strip() if label and full.startswith(label) else full
        at = parse_moodle_date(text, tz)
        page.dates.append(ActivityDate(label=label, text=text, at=at))
        lower = label.lower()
        if any(w in lower for w in _CUTOFF_WORDS):
            page.cutoff_at = at
        elif any(w in lower for w in _DUE_WORDS):
            page.due_at = at
        elif any(w in lower for w in _OPEN_WORDS):
            page.opens_at = at

    status_table = soup.select_one(".submissionstatustable table") or next(
        (t for t in soup.select("table.generaltable") if own(t)), None)
    if status_table is not None:
        page.submission_status = "not_submitted"
        for row in status_table.select("tr"):
            header, value = row.find("th"), row.find("td")
            if not header or not value:
                continue
            label, text = clean_text(header.get_text(" ")), clean_text(value.get_text(" "))
            if label:
                page.details[label] = text
            classes = set(value.get("class", []))
            for cls in classes:
                if cls.startswith("submissionstatus"):
                    status = cls.removeprefix("submissionstatus")
                    page.submission_status = {
                        "submitted": "submitted", "draft": "draft", "reopened": "reopened",
                        "new": "not_submitted", "": "not_submitted",
                    }.get(status, "unknown")
            if "submissiongraded" in classes:
                page.grading_status = "graded"
            elif "submissionnotgraded" in classes:
                page.grading_status = "not_graded"
            if classes & {"timeremaining", "overdue", "earlysubmission", "latesubmission"}:
                page.time_remaining = text
                page.overdue = "overdue" in classes or "latesubmission" in classes

    intro = soup.select_one(".activity-description, #intro")
    if intro is not None:
        page.description = _html_text(intro)[:4000] or None
    return page


async def fetch_assignment_page(client: MoodleClient, cmid: int, tz: ZoneInfo) -> AssignmentPage:
    html, final_path = await client.get_page("/mod/assign/view.php", {"id": cmid})
    if not final_path.endswith("/mod/assign/view.php"):
        # Moodle sent us elsewhere (usually the course page): not available to this user yet
        return AssignmentPage(accessible=False)
    return parse_assignment_page(html, tz)


# --- calendar ---------------------------------------------------------------


def cmid_from_url(url: str | None) -> int | None:
    if not url:
        return None
    values = parse_qs(urlparse(url).query).get("id")
    return int(values[0]) if values and values[0].isdigit() else None


async def fetch_action_events(client: MoodleClient, time_from: int, time_to: int) -> list[dict[str, Any]]:
    """Open to-dos (assignment due dates, quiz closes, ...) in a time range, all courses."""
    events: list[dict[str, Any]] = []
    after: int | None = None
    for _ in range(20):  # 20 pages x 50 events is plenty
        args: dict[str, Any] = {"timesortfrom": time_from, "timesortto": time_to, "limitnum": 50}
        if after:
            args["aftereventid"] = after
        data = await client.ajax("core_calendar_get_action_events_by_timesort", args)
        page = data.get("events", [])
        events.extend(page)
        if len(page) < 50:
            break
        after = page[-1]["id"]
    return events


# --- announcements ----------------------------------------------------------


@dataclass(frozen=True)
class Discussion:
    id: int
    subject: str
    author: str | None
    created_at: datetime | None
    modified_at: datetime | None


def is_news_forum(forum_html: str) -> bool:
    soup = BeautifulSoup(forum_html, "lxml")
    return soup.body is not None and "forumtype-news" in soup.body.get("class", [])


def parse_discussions(forum_html: str) -> list[Discussion]:
    soup = BeautifulSoup(forum_html, "lxml")
    result = []
    for row in soup.select("[data-region='discussion-list-item'][data-discussionid]"):
        did = row["data-discussionid"]
        if not str(did).isdigit():
            continue
        topic = row.select_one("td.topic") or row.select_one("a[href*='discuss.php']")
        author = row.select_one("td.author .author-info > div") or row.select_one("td.author")

        def ts(kind: str, row: Tag = row, did: str = did) -> datetime | None:
            el = row.select_one(f"#time-{kind}-{did}")
            return from_timestamp(el.get("data-timestamp")) if el else None

        result.append(Discussion(
            id=int(did),
            subject=clean_text(topic.get_text(" ")) if topic else "",
            author=clean_text(author.get_text(" ")) or None if author else None,
            created_at=ts("created"),
            modified_at=ts("modified"),
        ))
    return result


@dataclass
class Post:
    subject: str
    author: str | None
    message: str
    created_at: datetime | None
    attachments: list[Attachment]


def post_from_api(data: dict[str, Any]) -> Post:
    message_html = data.get("message") or ""
    soup = BeautifulSoup(f"<div>{message_html}</div>", "lxml")
    return Post(
        subject=clean_text(data.get("subject")),
        author=clean_text((data.get("author") or {}).get("fullname")) or None,
        message=_html_text(soup.div)[:6000] if soup.div else "",
        created_at=from_timestamp(data.get("timecreated")),
        attachments=[Attachment(filename=a.get("filename", ""), url=a.get("url", ""))
                     for a in data.get("attachments") or []],
    )


async def fetch_first_post(client: MoodleClient, discussion_id: int) -> Post | None:
    data = await client.ajax("mod_forum_get_discussion_posts",
                             {"discussionid": discussion_id, "sortby": "created", "sortdirection": "ASC"})
    posts = data.get("posts", [])
    first = next((p for p in posts if not p.get("parentid")), posts[0] if posts else None)
    return post_from_api(first) if first else None


# --- module updates ---------------------------------------------------------

_UPDATE_NAMES = {
    "configuration": "settings",
    "contentfiles": "files",
    "introfiles": "description files",
    "submissions": "submissions",
    "grades": "grades",
    "discussions": "discussions",
    "entries": "entries",
    "attempts": "attempts",
}


@dataclass(frozen=True)
class ModuleUpdate:
    cmid: int
    updates: list[str]
    updated_at: datetime | None


async def fetch_updates_since(client: MoodleClient, course_id: int, since: int) -> list[ModuleUpdate]:
    data = await client.ajax("core_course_get_updates_since", {"courseid": course_id, "since": since})
    result = []
    for instance in data.get("instances", []):
        if instance.get("contextlevel") != "module":
            continue
        updates = instance.get("updates", [])
        times = [u["timeupdated"] for u in updates if u.get("timeupdated")]
        result.append(ModuleUpdate(
            cmid=int(instance["id"]),
            updates=sorted({_UPDATE_NAMES.get(u["name"], u["name"]) for u in updates}),
            updated_at=from_timestamp(max(times)) if times else None,
        ))
    return result


# --- Moodle global search ---------------------------------------------------


@dataclass(frozen=True)
class GlobalSearchHit:
    title: str
    url: str
    cmid: int | None
    course_id: int | None
    course_name: str | None
    module: str | None
    snippet: str | None


_ICON_MODULE_RE = re.compile(r"/image\.php/[^/]+/(?:mod_)?([a-z0-9_]+)/")


def parse_global_search(search_html: str, base_url: str) -> list[GlobalSearchHit]:
    soup = BeautifulSoup(search_html, "lxml")
    hits = []
    for res in soup.select(".result"):
        link = res.select_one(".result-title a[href]")
        if link is None:
            continue
        url = urljoin(base_url + "/", link["href"])
        parsed = urlparse(url)
        if not (parsed.path.endswith("/course/view.php") or "/mod/" in parsed.path):
            continue  # people, blog entries, ...
        cmid = None
        if parsed.fragment.startswith("module-") and parsed.fragment[7:].isdigit():
            cmid = int(parsed.fragment[7:])
        elif "/mod/" in parsed.path:
            cmid = cmid_from_url(url)
        course_link = None
        for a in res.select(".result-context-info a[href*='course/view.php']"):
            course_link = a  # the last one carries the course name
        course_id = cmid_from_url(course_link["href"]) if course_link else None
        if course_id is None and parsed.path.endswith("/course/view.php"):
            course_id = cmid_from_url(url)
        course_name = None
        if course_link is not None:
            text = clean_text(course_link.get_text(" "))
            course_name = re.sub(r"^(im Kurs|in course|dans le cours|nel corso)\s+", "", text, flags=re.I)
        icon = res.select_one(".result-title img[src]")
        module = None
        if icon is not None:
            m = _ICON_MODULE_RE.search(icon["src"])
            module = m.group(1) if m and m.group(1) not in {"core", "theme"} else None
        snippet_el = res.select_one(".result-content")
        hits.append(GlobalSearchHit(
            title=clean_text(link.get_text(" ")),
            url=url,
            cmid=cmid,
            course_id=course_id,
            course_name=course_name,
            module=module,
            snippet=clean_text(snippet_el.get_text(" "))[:300] if snippet_el else None,
        ))
    return hits


async def global_search(client: MoodleClient, query: str, page: int = 0) -> list[GlobalSearchHit]:
    html = await client.get_html("/search/index.php", {"q": query, "page": page})
    return parse_global_search(html, client.base_url)
