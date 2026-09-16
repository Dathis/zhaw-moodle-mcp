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
