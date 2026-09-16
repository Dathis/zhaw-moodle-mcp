from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Deadline(BaseModel):
    course_id: int
    course_name: str
    activity_id: int | None = Field(default=None, description="cmid of the activity")
    activity_name: str
    module: str = Field(description="assign, quiz, ...")
    event: str = Field(description="Moodle event type, e.g. due (assignment) or close (quiz)")
    due_at: datetime
    overdue: bool
    action: str | None = Field(default=None, description="What Moodle asks you to do, e.g. 'Add submission'")
    actionable: bool = Field(description="Whether the action can be done right now")
    url: str


class DeadlineList(BaseModel):
    now: datetime
    until: datetime
    deadlines: list[Deadline]
    note: str = "Only open to-dos are listed; completed activities are omitted by Moodle."


class ActivityDate(BaseModel):
    label: str
    text: str
    at: datetime | None = None


SubmissionStatus = Literal["submitted", "draft", "not_submitted", "reopened", "unknown"]
GradingStatus = Literal["graded", "not_graded", "unknown"]


class Assignment(BaseModel):
    id: int = Field(description="cmid")
    course_id: int
    course_name: str
    section: str
    name: str
    url: str
    opens_at: datetime | None = None
    due_at: datetime | None = None
    cutoff_at: datetime | None = None
    dates: list[ActivityDate] = Field(default_factory=list, description="Dates as shown by Moodle")
    submitted: bool | None = None
    submission_status: SubmissionStatus = "unknown"
    grading_status: GradingStatus = "unknown"
    overdue: bool | None = None
    time_remaining: str | None = None
    details: dict[str, str] = Field(default_factory=dict, description="Status table as shown by Moodle")
    description: str | None = None
    accessible: bool = Field(default=True, description="False if Moodle does not let you open it yet "
                                                         "(e.g. access restrictions); details are then unknown")


class AssignmentList(BaseModel):
    assignments: list[Assignment]
    failed: list[str] = Field(default_factory=list, description="Assignments that could not be read")


class Attachment(BaseModel):
    filename: str
    url: str


class Announcement(BaseModel):
    course_id: int
    course_name: str
    discussion_id: int
    subject: str
    author: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None
    url: str
    message: str | None = Field(default=None, description="Plain text of the first post")
    attachments: list[Attachment] = Field(default_factory=list)


class AnnouncementList(BaseModel):
    announcements: list[Announcement]


class ModuleChange(BaseModel):
    id: int
    name: str
    module: str
    type: str
    section: str
    change: Literal["new", "updated", "changed"] = Field(
        description="'changed' when the server cannot tell new from updated (course indexed after 'since')")
    updates: list[str] = Field(description="What changed: files, settings, ...")
    updated_at: datetime | None = None
    url: str | None = None


class CourseChanges(BaseModel):
    course_id: int
    course_name: str
    modules: list[ModuleChange] = Field(default_factory=list)
    announcements: list[Announcement] = Field(default_factory=list)
    deadlines: list[Deadline] = Field(default_factory=list, description="New or changed open deadlines")


class RecentChanges(BaseModel):
    since: datetime
    until: datetime
    courses: list[CourseChanges]
    unchanged_courses: list[str] = Field(default_factory=list)
