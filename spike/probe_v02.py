"""Spike v0.2: which data sources for assignments, deadlines, announcements,
search and recent changes work with the session-authenticated AJAX service.

Uses the production client/session. Raw responses go to spike/output/v02/."""

import asyncio
import json
import re
import sys
import time
from collections import Counter

from bs4 import BeautifulSoup

from zhaw_moodle_mcp.auth.session import SessionStore
from zhaw_moodle_mcp.config import load_config
from zhaw_moodle_mcp.moodle.client import MoodleAjaxError, MoodleClient
from zhaw_moodle_mcp.moodle.courses import get_course_state, list_courses

from common import OUTPUT_DIR

OUT = OUTPUT_DIR / "v02"


def save(name: str, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False)
    (OUT / name).write_text(text, encoding="utf-8")


def keys(obj) -> list[str]:
    return sorted(obj.keys()) if isinstance(obj, dict) else []


async def try_ajax(c: MoodleClient, method: str, args: dict):
    try:
        data = await c.ajax(method, args)
    except MoodleAjaxError as exc:
        print(f"  {method}: FAILED {exc.errorcode}")
        return None
    save(f"{method}.json", data)
    size = len(data) if isinstance(data, (list, dict)) else "-"
    print(f"  {method}: OK (top-level keys={keys(data) or type(data).__name__}, len={size})")
    return data


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    c = MoodleClient(cfg.moodle, SessionStore(cfg.session.storage_state, cfg.browser.profile_directory))
    if not await c.check_session():
        print("session invalid - run `uv run zhaw-moodle-mcp login`")
        return 1

    courses = [x for x in await list_courses(c) if x.status == "active"]
    ids = [x.id for x in courses]
    print("courses:", ids)

    states = {cid: await get_course_state(c, cid) for cid in ids}
    by_module = Counter(cm["module"] for s in states.values() for cm in s["cm"])
    print("modules:", dict(by_module))
    assign_cm = next((cm, cid) for cid, s in states.items() for cm in s["cm"] if cm["module"] == "assign")
    forum_cms = [(cm, cid) for cid, s in states.items() for cm in s["cm"] if cm["module"] == "forum"]
    print("forums:", [(cid, cm["id"], cm["name"]) for cm, cid in forum_cms])

    now = int(time.time())
    print("\n== calendar / deadlines ==")
    ev = await try_ajax(c, "core_calendar_get_action_events_by_timesort",
                        {"timesortfrom": now - 86400, "timesortto": now + 60 * 86400, "limitnum": 50})
    if ev:
        events = ev.get("events", [])
        print("  events:", len(events), "event keys:", keys(events[0]) if events else None)
        for e in events[:8]:
            action = e.get("action") or {}
            print(f"   - {e.get('modulename')} cm={e.get('instance')}/{(e.get('url') or '')[-30:]} "
                  f"course={e.get('course', {}).get('id')} due={e.get('timesort')} "
                  f"type={e.get('eventtype')} action={action.get('name')!r} actionable={action.get('actionable')} "
                  f"overdue={e.get('overdue')}")
    await try_ajax(c, "core_calendar_get_action_events_by_courses",
                   {"courseids": ids, "timesortfrom": now - 86400, "limitnum": 20})
    up = await try_ajax(c, "core_calendar_get_calendar_upcoming_view", {"courseid": 1, "categoryid": 0})
    if up:
        print("  upcoming events:", len(up.get("events", [])),
              "keys:", keys(up["events"][0]) if up.get("events") else None)
    await try_ajax(c, "core_calendar_get_calendar_events",
                   {"events": {"courseids": ids}, "options": {"timestart": now, "timeend": now + 30 * 86400}})

    print("\n== assignments ==")
    await try_ajax(c, "mod_assign_get_assignments", {"courseids": ids})
    cm, cid = assign_cm
    await try_ajax(c, "mod_assign_get_submission_status", {"assignid": 0})
    await try_ajax(c, "core_course_get_module", {"id": int(cm["id"]), "sectionreturn": 0})
    await try_ajax(c, "core_course_get_course_module", {"cmid": int(cm["id"])})
    html = await c.get_html("/mod/assign/view.php", {"id": cm["id"]})
    save("assign_view.html", html)
    soup = BeautifulSoup(html, "lxml")
    print(f"  assign view cm={cm['id']}: tables={len(soup.select('table'))}, "
          f"generaltable={len(soup.select('table.generaltable'))}")
    for row in soup.select("table.generaltable tr")[:10]:
        cells = [x.get_text(' ', strip=True)[:50] for x in row.select('th,td')]
        classes = [x.get('class') for x in row.select('td')]
        print("   row:", cells, classes)
    dates = soup.select("[data-region='activity-dates'] div, .activity-dates div")
    print("  activity dates:", [d.get_text(' ', strip=True)[:60] for d in dates][:4])
    info = soup.select("[data-region='activity-information'], .activity-information")
    print("  activity info:", [i.get_text(' ', strip=True)[:80] for i in info][:2])

    print("\n== forums / announcements ==")
    fb = await try_ajax(c, "mod_forum_get_forums_by_courses", {"courseids": ids})
    news = []
    if fb:
        print("  forums:", [(f["course"], f["cmid"], f["type"], f["name"]) for f in fb])
        news = [f for f in fb if f["type"] == "news"]
    target = news[0] if news else None
    if target:
        disc = await try_ajax(c, "mod_forum_get_forum_discussions",
                              {"forumid": target["id"], "sortorder": -1, "page": 0, "perpage": 5})
        if disc:
            d = disc.get("discussions", [])
            print("  discussions:", len(d), "keys:", keys(d[0]) if d else None)
            if d:
                await try_ajax(c, "mod_forum_get_discussion_posts",
                               {"discussionid": d[0]["discussion"], "sortby": "created", "sortdirection": "ASC"})
        await try_ajax(c, "mod_forum_get_forum_discussions_paginated",
                       {"forumid": target["id"], "sortby": "timemodified", "sortdirection": "DESC",
                        "page": 0, "perpage": 5})
    forum_cm = forum_cms[0][0]
    html = await c.get_html("/mod/forum/view.php", {"id": forum_cm["id"]})
    save("forum_view.html", html)
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("[data-region='discussion-list-item'], tr.discussion")
    print(f"  forum view cm={forum_cm['id']}: discussion rows={len(rows)}, "
          f"data-forumtype={[x.get('data-forumtype') for x in soup.select('[data-forumtype]')][:2]}")

    print("\n== search / recent / notifications ==")
    await try_ajax(c, "core_search_get_results", {"query": "Übung", "filters": {}, "page": 0})
    await try_ajax(c, "core_search_view", {"query": "Übung"})
    await try_ajax(c, "message_popup_get_popup_notifications",
                   {"useridto": 0, "newestfirst": True, "limit": 10, "offset": 0})
    await try_ajax(c, "core_course_get_updates_since", {"courseid": ids[0], "since": now - 7 * 86400})
    await try_ajax(c, "core_course_check_updates",
                   {"courseid": ids[0], "tocheck": [{"contextlevel": "module", "id": int(cm["id"]),
                                                     "since": now - 30 * 86400}]})
    await try_ajax(c, "block_recentlyaccesseditems_get_recent_items", {"limit": 5})
    r = await c.http.get("/course/recent.php", params={"id": ids[0]})
    save("course_recent.html", r.text)
    print(f"  course/recent.php -> {r.status_code}, len={len(r.text)}, "
          f"has 'recent' heading={bool(re.search('recent', r.text, re.I))}")
    r = await c.http.get("/search/index.php", params={"q": "Übung"})
    save("search_index.html", r.text)
    print(f"  search/index.php -> {r.status_code}, results={len(BeautifulSoup(r.text, 'lxml').select('.result'))}")

    await c.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
