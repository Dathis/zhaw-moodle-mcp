"""Spike: reuse the exported browser cookies with httpx and check which Moodle
data sources work without a browser (AJAX service vs. HTML parsing, downloads).

Never prints cookie values or the sesskey. Raw responses go to spike/output/."""

import json
import re
import sys
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from common import BASE_URL, OUTPUT_DIR, STORAGE_STATE

MOODLE_HOST = urlparse(BASE_URL).hostname


def load_cookies() -> httpx.Cookies:
    state = json.loads(STORAGE_STATE.read_text(encoding="utf-8"))
    jar = httpx.Cookies()
    for c in state["cookies"]:
        domain = c["domain"].lstrip(".")
        if MOODLE_HOST == domain or MOODLE_HOST.endswith("." + domain):
            jar.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
    return jar


def save(name: str, content: str | bytes) -> None:
    path = OUTPUT_DIR / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def ajax(client: httpx.Client, sesskey: str, method: str, args: dict):
    """Call Moodle's session-authenticated AJAX endpoint. Returns (ok, data_or_error)."""
    r = client.post(
        "/lib/ajax/service.php",
        params={"sesskey": sesskey, "info": method},
        json=[{"index": 0, "methodname": method, "args": args}],
    )
    body = r.json()
    if isinstance(body, dict):  # top-level failure, e.g. invalid session
        return False, f"{body.get('errorcode')}: {body.get('error')}"
    item = body[0]
    if item.get("error"):
        exc = item.get("exception", {})
        return False, f"{exc.get('errorcode')}: {exc.get('message')}"
    return True, item["data"]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(
        base_url=BASE_URL,
        cookies=load_cookies(),
        follow_redirects=True,
        timeout=30,
        headers={"User-Agent": "zhaw-moodle-mcp-spike/0.1"},
    )

    section("Session check")
    r = client.get("/my/")
    final = urlparse(str(r.url))
    soup = BeautifulSoup(r.text, "lxml")
    user_id = re.search(r'"userId":(\d+)', r.text)
    user_id = int(user_id.group(1)) if user_id else 0
    has_login_link = soup.select_one('a[href$="/login/index.php"]') is not None
    logged_in = final.hostname == MOODLE_HOST and user_id > 1 and not has_login_link
    print(f"GET /my/ -> {r.status_code} {final.hostname}{final.path}, "
          f"userId>1={user_id > 1}, login_link={has_login_link}, logged_in={logged_in}")
    m = re.search(r'"sesskey":"([^"]+)"', r.text)
    if not (logged_in and m):
        print("Session invalid - run login.py first")
        return 1
    sesskey = m.group(1)
    save("dashboard.html", r.text)
    version = re.search(r'"version":"?(\d{10})', r.text)
    theme = re.search(r'"theme":"([^"]+)"', r.text)
    print(f"sesskey found; theme={theme and theme.group(1)}; version hint={version and version.group(1)}")

    section("AJAX: courses")
    courses = {}
    for cls in ("inprogress", "past", "future", "all"):
        ok, data = ajax(
            client, sesskey,
            "core_course_get_enrolled_courses_by_timeline_classification",
            {"classification": cls, "limit": 0, "offset": 0, "sort": "fullname"},
        )
        if not ok:
            print(f"{cls}: FAILED {data}")
            continue
        save(f"courses_{cls}.json", json.dumps(data, indent=2, ensure_ascii=False))
        print(f"{cls}: {len(data['courses'])} courses")
        courses[cls] = data["courses"]
    for c in courses.get("inprogress", []):
        print(f"  [{c['id']}] {c['shortname']} | {c['fullname']} | hidden={c.get('hidden')}")
    if courses.get("all"):
        print(f"course fields: {sorted(courses['all'][0].keys())}")

    if not courses.get("inprogress"):
        print("No in-progress courses, stopping")
        return 1
    course_id = courses["inprogress"][0]["id"]

    section(f"AJAX: course contents for {course_id}")
    candidates = [
        ("core_course_get_contents", {"courseid": course_id}),
        ("core_courseformat_get_state", {"courseid": course_id}),
        ("core_webservice_get_site_info", {}),
        ("core_calendar_get_action_events_by_timesort",
         {"timesortfrom": 0, "limitnum": 20}),
    ]
    state = None
    for method, args in candidates:
        ok, data = ajax(client, sesskey, method, args)
        print(f"{method}: {'OK' if ok else 'FAILED ' + str(data)}")
        if ok:
            save(f"{method}.json", json.dumps(data, indent=2, ensure_ascii=False))
            if method == "core_courseformat_get_state":
                state = json.loads(data)

    cms = []
    if state:
        print(f"state keys: {sorted(state.keys())}")
        print(f"sections={len(state.get('section', []))} cms={len(state.get('cm', []))}")
        cms = state.get("cm", [])
        if cms:
            print(f"cm fields: {sorted(cms[0].keys())}")
        for s in state.get("section", [])[:5]:
            print(f"  section {s.get('number')}: {s.get('title')!r} cms={len(s.get('cmlist', []))}")

    section(f"HTML: course/view.php?id={course_id}")
    r = client.get("/course/view.php", params={"id": course_id})
    save("course_view.html", r.text)
    soup = BeautifulSoup(r.text, "lxml")
    activities = soup.select("li.activity")
    print(f"status={r.status_code} li.activity={len(activities)}")
    modtypes = {}
    for li in activities:
        mt = next((c for c in li.get("class", []) if c.startswith("modtype_")), "?")
        modtypes[mt] = modtypes.get(mt, 0) + 1
    print(f"modtypes: {modtypes}")
    html_sections = soup.select("li.section, [data-for='section']")
    print(f"section elements: {len(html_sections)}")

    section("Download: first mod/resource")
    resource_ids = [c["id"] for c in cms if c.get("module") == "resource"]
    if not resource_ids:
        resource_ids = [
            li.get("id", "").removeprefix("module-")
            for li in activities if "modtype_resource" in li.get("class", [])
        ]
    if resource_ids:
        cmid = resource_ids[0]
        r = client.get("/mod/resource/view.php", params={"id": cmid, "redirect": 1})
        final = urlparse(str(r.url))
        cd = r.headers.get("content-disposition", "")
        fname = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
        fname = unquote(fname.group(1)) if fname else unquote(final.path.rsplit("/", 1)[-1])
        print(f"cmid={cmid} -> {r.status_code} {final.path.split('/')[1]}/..., "
              f"type={r.headers.get('content-type')}, size={len(r.content)}, "
              f"last-modified={r.headers.get('last-modified')}, etag={bool(r.headers.get('etag'))}")
        print(f"filename={fname!r}")
        if r.status_code == 200 and "text/html" not in r.headers.get("content-type", ""):
            safe_name = re.sub(r"[^\w.-]", "_", fname)
            save(f"download_{cmid}_{safe_name}", r.content)
            print("saved to spike/output/")
        else:
            save(f"resource_{cmid}.html", r.text)
            print("got HTML instead of a file (saved for inspection)")
    else:
        print("no mod/resource in this course")

    section("Folder: first mod/folder")
    folder_ids = [c["id"] for c in cms if c.get("module") == "folder"]
    if folder_ids:
        r = client.get("/mod/folder/view.php", params={"id": folder_ids[0]})
        save(f"folder_{folder_ids[0]}.html", r.text)
        links = BeautifulSoup(r.text, "lxml").select("a[href*='pluginfile.php']")
        print(f"cmid={folder_ids[0]} -> {r.status_code}, pluginfile links={len(links)}")
    else:
        print("no mod/folder in this course")

    return 0


if __name__ == "__main__":
    sys.exit(main())
