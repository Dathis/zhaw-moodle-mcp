"""Courses and course structure via Moodle's AJAX service."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from ..errors import ErrorCode, MoodleError
from ..models import Course, CourseModule, CourseSection, CourseStructure
from ..models.course import CourseStatus
from .client import MoodleAjaxError, MoodleClient
from .parser import DOWNLOADABLE_MODULES, MODULE_TYPES, clean_text, parse_activity_icons, type_from_icon

# Moodle timeline classification -> our status
_CLASSIFICATIONS: dict[str, CourseStatus] = {"inprogress": "active", "past": "past", "future": "future"}


def _timestamp(value: Any) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if isinstance(value, int) and value > 0 else None


def course_from_api(data: dict[str, Any], status: CourseStatus) -> Course:
    return Course(
        id=int(data["id"]),
        name=clean_text(data.get("fullname")),
        short_name=clean_text(data.get("shortname")),
        url=data.get("viewurl", ""),
        status=status,
        category=clean_text(data.get("coursecategory")) or None,
        start_date=_timestamp(data.get("startdate")),
        end_date=_timestamp(data.get("enddate")),
        favourite=bool(data.get("isfavourite")),
    )


async def list_courses(client: MoodleClient) -> list[Course]:
    async def fetch(classification: str) -> list[dict[str, Any]]:
        data = await client.ajax(
            "core_course_get_enrolled_courses_by_timeline_classification",
            {"classification": classification, "limit": 0, "offset": 0, "sort": "fullname"},
        )
        return data.get("courses", [])

    results = await asyncio.gather(*(fetch(c) for c in _CLASSIFICATIONS))
    courses: dict[int, Course] = {}
    for status, items in zip(_CLASSIFICATIONS.values(), results, strict=True):
        for item in items:
            courses.setdefault(int(item["id"]), course_from_api(item, status))
    return sorted(courses.values(), key=lambda c: (c.status != "active", c.name.lower()))


async def get_course_state(client: MoodleClient, course_id: int) -> dict[str, Any]:
    try:
        raw = await client.ajax("core_courseformat_get_state", {"courseid": course_id})
    except MoodleAjaxError as exc:
        raise MoodleError(ErrorCode.COURSE_NOT_FOUND, f"Course {course_id} not found or not accessible.") from exc
    return json.loads(raw) if isinstance(raw, str) else raw


async def get_activity_icons(client: MoodleClient, course_id: int) -> dict[int, str]:
    html = await client.get_html("/course/view.php", {"id": course_id})
    return parse_activity_icons(html)


def module_from_state(cm: dict[str, Any], icons: dict[int, str]) -> CourseModule:
    cmid = int(cm["id"])
    module = cm.get("module", "")
    if module == "resource":
        type_ = type_from_icon(icons.get(cmid))
    else:
        type_ = MODULE_TYPES.get(module, module)
    # uservisible is false e.g. for activities locked by access restrictions
    accessible = bool(cm.get("uservisible", True))
    return CourseModule(
        id=cmid,
        name=clean_text(cm.get("name")),
        module=module,
        type=type_,
        url=cm.get("url") or None,
        downloadable=module in DOWNLOADABLE_MODULES and accessible,
        visible=bool(cm.get("visible", True)) and accessible,
    )


def build_structure(course: Course, state: dict[str, Any], icons: dict[int, str]) -> CourseStructure:
    cms = {str(cm["id"]): cm for cm in state.get("cm", [])}
    sections: dict[str, CourseSection] = {}
    parents: dict[str, str] = {}
    for s in state.get("section", []):
        sid = str(s["id"])
        modules = [
            module_from_state(cms[cmid], icons)
            for cmid in s.get("cmlist", [])
            if cmid in cms and cms[cmid].get("module") != "subsection"  # represented as subsection
        ]
        sections[sid] = CourseSection(
            id=int(sid),
            number=int(s.get("number", 0)),
            title=clean_text(s.get("title")) or f"Section {s.get('number')}",
            visible=bool(s.get("visible", True)),
            modules=modules,
        )
        if s.get("component") and s.get("parentsectionid"):
            parents[sid] = str(s["parentsectionid"])

    roots: list[CourseSection] = []
    for sid, section in sections.items():
        parent = sections.get(parents.get(sid, ""))
        (parent.subsections if parent else roots).append(section)
    return CourseStructure(course=course, sections=roots)
