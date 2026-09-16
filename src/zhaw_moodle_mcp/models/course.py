from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CourseStatus = Literal["active", "past", "future"]


class Course(BaseModel):
    id: int
    name: str
    short_name: str
    url: str
    status: CourseStatus
    category: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    favourite: bool = False


class CourseList(BaseModel):
    courses: list[Course]


class CourseModule(BaseModel):
    id: int = Field(description="Course module id (cmid); use as resource_id for downloadable modules")
    name: str
    module: str = Field(description="Moodle module type, e.g. resource, folder, url, page, quiz, assign")
    type: str = Field(description="Normalised type: pdf, powerpoint, word, excel, zip, file, folder, link, page, ...")
    url: str | None = None
    downloadable: bool
    visible: bool = True
    text: str | None = Field(default=None, description="Text of a label (text block) as Markdown")
    text_truncated: bool = Field(default=False, description="Full text via moodle_get_content")


class CourseSection(BaseModel):
    id: int
    number: int
    title: str
    visible: bool = True
    modules: list[CourseModule] = Field(default_factory=list)
    subsections: list[CourseSection] = Field(default_factory=list)


class CourseStructure(BaseModel):
    course: Course
    sections: list[CourseSection]


class ContentLink(BaseModel):
    text: str
    url: str
    kind: Literal["file", "activity", "moodle", "external"] = Field(
        description="file = file stored in Moodle, activity = link to another Moodle activity "
                    "(see activity_id), moodle = other Moodle page, external = outside Moodle")
    activity_id: int | None = None
    resource_id: str | None = Field(
        default=None, description="For files: id for moodle_download_resource ('<activity id>/<file path>')")


class ActivityContent(BaseModel):
    id: int
    course_id: int
    course_name: str
    section: str
    name: str
    module: str = Field(description="page or label")
    url: str | None = None
    text: str = Field(description="Content as Markdown")
    links: list[ContentLink] = Field(default_factory=list)
    modified_at: datetime | None = None
    accessible: bool = True
    note: str | None = None
