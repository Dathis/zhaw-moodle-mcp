from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from zhaw_moodle_mcp.moodle.activities import (
    cmid_from_url,
    is_news_forum,
    parse_assignment_page,
    parse_discussions,
    parse_global_search,
    post_from_api,
)
from zhaw_moodle_mcp.moodle.dates import parse_moodle_date, parse_since

from .conftest import BASE, LOGGED_IN_PAGE

TZ = ZoneInfo("Europe/Zurich")


@pytest.mark.parametrize(("text", "expected"), [
    ("Sonntag, 8. November 2026, 23:59", datetime(2026, 11, 8, 23, 59, tzinfo=TZ)),
    ("Montag, 14. September 2026, 15:45", datetime(2026, 9, 14, 15, 45, tzinfo=TZ)),
    ("Sunday, 8 November 2026, 11:59 PM", datetime(2026, 11, 8, 23, 59, tzinfo=TZ)),
    ("Monday, 5 October 2026, 12:05 AM", datetime(2026, 10, 5, 0, 5, tzinfo=TZ)),
    ("dimanche, 8 novembre 2026, 23:59", datetime(2026, 11, 8, 23, 59, tzinfo=TZ)),
    ("1. März 2027", datetime(2027, 3, 1, 0, 0, tzinfo=TZ)),
])
def test_parse_moodle_date(text, expected):
    assert parse_moodle_date(text, TZ) == expected


@pytest.mark.parametrize("text", ["Verbleibend: 53 Tage 11 Stunden", "", "31. Foo 2026, 10:00", "31. Februar 2026"])
def test_parse_moodle_date_rejects(text):
    assert parse_moodle_date(text, TZ) is None


def test_parse_since():
    assert parse_since("2026-09-14", TZ) == datetime(2026, 9, 14, tzinfo=TZ)
    assert parse_since("2026-09-14T08:30", TZ) == datetime(2026, 9, 14, 8, 30, tzinfo=TZ)
    assert parse_since("2026-09-14T08:30+00:00", TZ).utcoffset() == timedelta(0)
    assert abs(parse_since("7d", TZ) - (datetime.now(UTC) - timedelta(days=7))) < timedelta(seconds=5)
    assert abs(parse_since("2w", TZ) - (datetime.now(UTC) - timedelta(weeks=2))) < timedelta(seconds=5)
    with pytest.raises(ValueError):
        parse_since("last monday", TZ)


def assign_page(dates: str, rows: str, intro: str = "", course_listing: str = "") -> str:
    return LOGGED_IN_PAGE.replace("</body>", f"""
      <div class="activity-header">
        <div class="activity-dates" data-region="activity-dates">{dates}</div>
      </div>
      <div class="activity-description" id="intro">{intro}</div>
      <div class="submissionstatustable"><table class="generaltable">{rows}</table></div>
      <ul>{course_listing}</ul></body>""")


SUBMITTED = assign_page(
    dates="<div><strong>Geöffnet:</strong> Montag, 7. September 2026, 00:00</div>"
          "<div><strong>Fällig:</strong> Sonntag, 8. November 2026, 23:59</div>",
    rows="""
      <tr><th>Abgabestatus</th><td class="submissionstatussubmitted cell c1">Zur Bewertung abgegeben</td></tr>
      <tr><th>Bewertungsstatus</th><td class="submissiongraded cell c1">Bewertet</td></tr>
      <tr><th>Verbleibende Zeit</th><td class="earlysubmission cell c1">2 Tage früher abgegeben</td></tr>""",
    intro="<p>Bitte laden Sie Ihre <b>Präsentation</b> hoch.</p><ul><li>PDF</li><li>max. 10 MB</li></ul>",
    course_listing="""<li class="activity modtype_quiz"><div data-region="activity-dates">
      <div><strong>Schließt:</strong> Sonntag, 1. November 2026, 23:59</div></div>
      <table class="generaltable"><tr><th>x</th><td class="submissionstatusdraft">y</td></tr></table></li>""",
)


def test_parse_submitted_assignment():
    page = parse_assignment_page(SUBMITTED, TZ)
    assert [d.label for d in page.dates] == ["Geöffnet", "Fällig"]
    assert page.opens_at == datetime(2026, 9, 7, tzinfo=TZ)
    assert page.due_at == datetime(2026, 11, 8, 23, 59, tzinfo=TZ)
    assert page.dates[1].text == "Sonntag, 8. November 2026, 23:59"
    assert page.submission_status == "submitted"
    assert page.grading_status == "graded"
    assert page.overdue is False
    assert page.time_remaining == "2 Tage früher abgegeben"
    assert page.details["Abgabestatus"] == "Zur Bewertung abgegeben"
    assert page.description == "Bitte laden Sie Ihre Präsentation hoch.\n- PDF\n- max. 10 MB"


def test_parse_open_overdue_assignment():
    page = parse_assignment_page(assign_page(
        dates="<div><strong>Due:</strong> Friday, 2 October 2026, 11:59 PM</div>",
        rows="""<tr><th>Submission status</th><td class="cell c1">No attempt</td></tr>
                <tr><th>Grading status</th><td class="submissionnotgraded cell">Not graded</td></tr>
                <tr><th>Time remaining</th><td class="overdue cell">Assignment is overdue by: 2 days</td></tr>"""), TZ)
    assert page.due_at == datetime(2026, 10, 2, 23, 59, tzinfo=TZ)
    assert page.submission_status == "not_submitted"
    assert page.grading_status == "not_graded"
    assert page.overdue is True


def test_parse_assignment_without_status_table():
    page = parse_assignment_page(LOGGED_IN_PAGE, TZ)
    assert page.submission_status == "unknown" and page.dates == [] and page.description is None


FORUM = LOGGED_IN_PAGE.replace("<body>", '<body class="format-topcoll forumtype-news path-mod">').replace(
    "</body>", """<table>
    <tr data-region="discussion-list-item" data-discussionid="204711">
      <td class="topic"><a href="/mod/forum/discuss.php?d=204711">Willkommen &amp; Start</a></td>
      <td class="author"><div class="author-info"><div class="text-truncate">Anna Beispiel</div>
        <div><time id="time-created-204711" data-timestamp="1788959558">9. Sept.</time></div></div></td>
      <td><time id="time-modified-204711" data-timestamp="1788960000">9. Sept.</time></td>
    </tr>
    <tr data-region="discussion-list-item" data-discussionid="">x</tr>
    </table></body>""")


def test_news_forum_and_discussions():
    assert is_news_forum(FORUM)
    assert not is_news_forum(LOGGED_IN_PAGE)
    [d] = parse_discussions(FORUM)
    assert (d.id, d.subject, d.author) == (204711, "Willkommen & Start", "Anna Beispiel")
    assert d.created_at == datetime.fromtimestamp(1788959558, UTC)
    assert d.modified_at == datetime.fromtimestamp(1788960000, UTC)


def test_post_from_api():
    post = post_from_api({
        "subject": "Hallo", "author": {"fullname": "Prof. X"}, "timecreated": 1788959558,
        "message": "<p>Liebe Studierende,</p><p>die Vorlesung<br>fällt aus.</p>",
        "attachments": [{"filename": "plan.pdf", "url": f"{BASE}/pluginfile.php/1/plan.pdf"}],
    })
    assert post.message == "Liebe Studierende,\ndie Vorlesung fällt aus."
    assert post.author == "Prof. X"
    assert post.attachments[0].filename == "plan.pdf"


SEARCH = f"""<div class="result"><h4 class="result-title">
  <img class="icon" src="{BASE}/theme/image.php/boost_union/assign/1789/monologo">
  <a href="{BASE}/mod/assign/view.php?id=2036588">Abgabe <span class="highlight">Übung</span> 0</a></h4>
  <div class="result-content">Laden Sie die <span>Übung</span> hoch</div>
  <div class="result-context-info"><a href="{BASE}/course/view.php?id=29890">Ergebnis im Kontext anzeigen</a> -
  <a href="{BASE}/course/view.php?id=29890">im Kurs Software Engineering 1 (2026-HS)</a></div></div>
<div class="result"><h4 class="result-title">
  <img class="icon" src="{BASE}/theme/image.php/boost_union/label/1789/monologo">
  <a href="{BASE}/course/view.php?id=29037#module-1989637">Übung in Kleinklasse</a></h4>
  <div class="result-context-info"><a href="{BASE}/course/view.php?id=29037">im Kurs STS</a></div></div>
<div class="result"><h4 class="result-title">
  <a href="{BASE}/user/view.php?id=5&amp;course=1">Max Muster</a></h4></div>"""


def test_parse_global_search():
    hits = parse_global_search(SEARCH, BASE)
    assert [(h.title, h.cmid, h.course_id, h.module) for h in hits] == [
        ("Abgabe Übung 0", 2036588, 29890, "assign"),
        ("Übung in Kleinklasse", 1989637, 29037, "label"),
    ]
    assert hits[0].course_name == "Software Engineering 1 (2026-HS)"
    assert hits[0].snippet == "Laden Sie die Übung hoch"


def test_cmid_from_url():
    assert cmid_from_url(f"{BASE}/mod/quiz/view.php?id=12") == 12
    assert cmid_from_url(None) is None
    assert cmid_from_url(f"{BASE}/calendar/view.php") is None
