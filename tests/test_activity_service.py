import time
from datetime import UTC, datetime, timedelta

import pytest
import respx

from zhaw_moodle_mcp.errors import MoodleError
from zhaw_moodle_mcp.service import MoodleService

from .fake_moodle import COURSE_ID, FakeFile, FakeMoodle
from .test_activities import FORUM, SEARCH, SUBMITTED

NOW = int(time.time())
DAY = 86400
# The announcement must stay inside the "last 7 days" window whenever the tests run
POSTED = NOW - DAY
NEWS_FORUM = FORUM.replace("1788959558", str(POSTED - 442)).replace("1788960000", str(POSTED))


def event(cmid, name, module, eventtype, due, *, overdue=False, modified=None, action="Add submission"):
    return {
        "id": cmid * 10, "name": f"{name} is due", "activityname": name, "modulename": module,
        "eventtype": eventtype, "timesort": due, "overdue": overdue, "timemodified": modified or NOW - 30 * DAY,
        "course": {"id": COURSE_ID, "fullname": "Test: Kurs"},
        "action": {"name": action, "actionable": True},
        "url": f"https://moodle.example.ch/mod/{module}/view.php?id={cmid}",
    }


def update(cmid, *names, at):
    return {"contextlevel": "module", "id": cmid, "updates": [{"name": n, "timeupdated": at} for n in names]}


@pytest.fixture
def moodle():
    fake = FakeMoodle(
        resources={11: ("Semesterplan", "Semesterplan.pdf", FakeFile(b"plan"))},
        folder={"Blatt1.pdf": FakeFile(b"b1")},
        extra_cms=[
            {"id": "80", "name": "Abgabe Präsentation", "module": "assign", "url": "", "visible": True},
            {"id": "81", "name": "Abschluss (gesperrt)", "module": "assign", "url": "", "visible": True},
            {"id": "94", "name": "Fragen", "module": "forum", "url": "", "visible": True},
            {"id": "95", "name": "Ankündigungen", "module": "forum", "url": "", "visible": True},
            {"id": "70", "name": "MC-Test 01", "module": "quiz", "url": "", "visible": True},
        ],
        events=[
            event(80, "Abgabe Präsentation", "assign", "due", NOW + 3 * DAY, modified=NOW - DAY),
            event(70, "MC-Test 01", "quiz", "close", NOW + 20 * DAY, action="Attempt quiz now"),
            event(71, "Old quiz", "quiz", "close", NOW - 2 * DAY, overdue=True),
        ],
        updates=[
            update(11, "configuration", "contentfiles", at=NOW - 2 * DAY),
            update(70, "configuration", at=NOW - 20 * DAY),
        ],
        forums={
            94: NEWS_FORUM.replace("forumtype-news", "forumtype-general"),
            95: NEWS_FORUM,
        },
        posts={204711: [
            {"id": 2, "parentid": 1, "subject": "Re", "message": "<p>reply</p>", "author": {"fullname": "S"}},
            {"id": 1, "parentid": 0, "subject": "Willkommen", "message": "<p>Hallo zusammen</p>",
             "author": {"fullname": "Anna Beispiel"}, "timecreated": POSTED - 442, "attachments": []},
        ]},
        assign_pages={80: SUBMITTED, 81: None},
        search_html=SEARCH,
    )
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
        yield fake


@pytest.fixture
async def service(config, store):
    svc = MoodleService(config)
    yield svc
    await svc.aclose()


async def test_deadlines(service, moodle):
    result = await service.activities.deadlines(14, None, include_overdue=False)
    assert [d.activity_name for d in result.deadlines] == ["Abgabe Präsentation"]
    d = result.deadlines[0]
    assert (d.course_name, d.activity_id, d.module, d.event, d.action) == (
        "Test: Kurs", 80, "assign", "due", "Add submission")

    result = await service.activities.deadlines(30, None, include_overdue=True)
    assert [d.activity_name for d in result.deadlines] == ["Old quiz", "Abgabe Präsentation", "MC-Test 01"]
    assert result.deadlines[0].overdue
    assert (await service.activities.deadlines(30, 999, True)).deadlines == []


async def test_assignments(service, moodle):
    result = await service.activities.assignments(None)
    by_id = {a.id: a for a in result.assignments}
    ok, locked = by_id[80], by_id[81]
    assert ok.submitted is True and ok.submission_status == "submitted" and ok.grading_status == "graded"
    assert ok.due_at is not None and ok.section == "Woche 1" and ok.accessible
    assert ok.url.endswith("/mod/assign/view.php?id=80")
    assert not locked.accessible and locked.submitted is None and locked.due_at is None
    assert [a.id for a in result.assignments] == [80, 81]  # with due date first
    assert result.failed == []


async def test_announcements_finds_news_forum_and_caches_it(service, moodle):
    result = await service.activities.announcements(None, None, limit=10)
    [a] = result.announcements
    assert (a.subject, a.author, a.message) == ("Willkommen & Start", "Anna Beispiel", "Hallo zusammen")
    assert a.url.endswith("/mod/forum/discuss.php?d=204711")
    assert moodle.page_views == {"/mod/forum/view.php?id=94": 1, "/mod/forum/view.php?id=95": 1}

    await service.activities.announcements(None, None, limit=10)
    assert moodle.page_views["/mod/forum/view.php?id=94"] == 1  # general forum is not read again
    assert moodle.page_views["/mod/forum/view.php?id=95"] == 2

    newer = datetime.fromtimestamp(POSTED, UTC) + timedelta(seconds=1)
    assert (await service.activities.announcements(None, newer, limit=10)).announcements == []


async def test_course_without_news_forum(service, moodle):
    moodle.forums = {94: FORUM.replace("forumtype-news", "forumtype-general")}
    moodle.extra_cms = [cm for cm in moodle.extra_cms if cm["id"] != "95"]
    assert (await service.activities.announcements(None, None, limit=10)).announcements == []


async def test_recent_changes_first_run_cannot_tell_new_from_updated(service, moodle):
    result = await service.activities.recent_changes("7d", None)
    [course] = result.courses
    assert [(m.id, m.change, m.updates) for m in course.modules] == [(11, "changed", ["files", "settings"])]
    assert [d.activity_name for d in course.deadlines] == ["Abgabe Präsentation"]
    assert [a.subject for a in course.announcements] == ["Willkommen & Start"]


async def test_recent_changes_new_vs_updated(service, moodle):
    await service.get_course(COURSE_ID)  # index the course
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    service.db.conn.execute("UPDATE courses SET first_indexed_at = ?", (old,))
    service.db.conn.execute("UPDATE modules SET first_seen_at = ?", (old,))
    service.db.conn.commit()

    moodle.extra_cms.append({"id": "60", "name": "Neue Folien", "module": "resource", "url": "", "visible": True})
    moodle.updates.append(update(60, "configuration", "contentfiles", at=NOW - DAY))
    result = await service.activities.recent_changes((datetime.now(UTC) - timedelta(days=7)).isoformat(), None)
    changes = {m.id: m.change for m in result.courses[0].modules}
    assert changes == {60: "new", 11: "updated"}


async def test_recent_changes_invalid_since(service, moodle):
    with pytest.raises(MoodleError):
        await service.activities.recent_changes("last monday", None)


async def test_recent_changes_unchanged_course(service, moodle):
    moodle.updates, moodle.events, moodle.forums = [], [], {}
    result = await service.activities.recent_changes("1d", None)
    assert result.courses == [] and result.unchanged_courses == ["Test: Kurs"]


async def test_search_index_refreshes_and_finds_modules_and_files(service, moodle):
    result = await service.activities.search("semesterplan", None, None, 10, "auto", False)
    assert result.source == "index"
    assert [(h.kind, h.id, h.type) for h in result.hits] == [("module", "11", "pdf")]
    assert result.hits[0].downloadable and result.hits[0].section == "Woche 1"

    # folder files become searchable once listed
    await service.list_resources(COURSE_ID, include_folder_contents=True)
    result = await service.activities.search("blatt", None, {"file"}, 10, "index", False)
    assert [h.id for h in result.hits] == ["90/Blatt1.pdf"]

    quiz = await service.activities.search("mc test", None, None, 10, "index", False)
    assert [h.name for h in quiz.hits] == ["MC-Test 01"]


async def test_search_falls_back_to_moodle(service, moodle):
    result = await service.activities.search("Kleinklasse", None, None, 10, "auto", False)
    assert result.source == "moodle"
    assert [h.id for h in result.hits] == ["2036588", "1989637"]
    only = await service.activities.search("x", 29037, None, 10, "moodle", False)
    assert [h.id for h in only.hits] == ["1989637"]


async def test_search_uses_index_age(service, moodle, config):
    await service.activities.search("plan", None, None, 10, "index", False)
    state_calls = moodle.page_views.copy()
    await service.activities.search("plan", None, None, 10, "index", False)
    assert moodle.page_views == state_calls  # index still fresh: no course reload

    config.search.index_max_age_minutes = 0
    await service.activities.search("plan", None, None, 10, "index", False)


async def test_sync_all(service, moodle, config):
    result = await service.sync_all(dry_run=False, update_changed=True)
    [course] = result.courses
    assert (course.course_id, course.new, course.failed, course.error) == (COURSE_ID, 2, 0, None)
    assert sorted(course.new_files) == ["Blatt1.pdf", "Semesterplan"]
    again = await service.sync_all(dry_run=False, update_changed=True)
    assert again.courses[0].unchanged == 2
