from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Resource(BaseModel):
    id: str = Field(description="'<cmid>' for modules, '<cmid>/<path>' for files inside a folder")
    course_id: int
    section: str = Field(description="Section path, subsections joined with ' / '")
    name: str
    type: str
    module: str
    url: str
    downloadable: bool
    folder_id: int | None = Field(default=None, description="cmid of the containing folder")
    filename: str | None = None
    size: int | None = None
    modified_at: datetime | None = None
    local_path: str | None = Field(default=None, description="Where the file was last downloaded to")


class ResourceList(BaseModel):
    course_id: int
    resources: list[Resource]


class DownloadedFile(BaseModel):
    resource_id: str
    path: str
    size: int
    status: Literal["downloaded", "unchanged"]


class DownloadResult(BaseModel):
    resource_id: str
    files: list[DownloadedFile]
