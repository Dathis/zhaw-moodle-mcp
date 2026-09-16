from __future__ import annotations

from pydantic import BaseModel, Field


class SyncChange(BaseModel):
    resource_id: str
    name: str
    section: str
    path: str | None = None
    detail: str | None = None


class SyncResult(BaseModel):
    course_id: int
    course_name: str
    dry_run: bool
    new: list[SyncChange] = Field(default_factory=list)
    updated: list[SyncChange] = Field(default_factory=list)
    restored: list[SyncChange] = Field(default_factory=list, description="Known files missing locally, re-downloaded")
    renamed: list[SyncChange] = Field(default_factory=list)
    removed: list[SyncChange] = Field(default_factory=list, description="Gone from Moodle; local files are kept")
    failed: list[SyncChange] = Field(default_factory=list)
    unchanged: int = 0
    download_directory: str


class CourseSyncSummary(BaseModel):
    course_id: int
    course_name: str
    new: int = 0
    updated: int = 0
    restored: int = 0
    renamed: int = 0
    removed: int = 0
    failed: int = 0
    unchanged: int = 0
    new_files: list[str] = Field(default_factory=list, description="Names of new/updated files (max 20)")
    failed_items: list[SyncChange] = Field(default_factory=list)
    error: str | None = Field(default=None, description="Set if the whole course could not be synced")


class SyncAllResult(BaseModel):
    dry_run: bool
    courses: list[CourseSyncSummary]
    download_directory: str
