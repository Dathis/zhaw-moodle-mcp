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
