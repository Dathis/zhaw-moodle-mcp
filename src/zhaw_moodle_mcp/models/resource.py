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


class DownloadFailure(BaseModel):
    resource_id: str
    error: str


class DownloadResult(BaseModel):
    resource_id: str
    files: list[DownloadedFile]
    failed: list[DownloadFailure] = Field(default_factory=list, description="Linked files that could not be loaded")


class FileText(BaseModel):
    resource_id: str
    path: str = Field(description="Local file the text was read from")
    file_type: str = Field(description="pdf, powerpoint, word, html or text")
    text: str = Field(description="Extracted text; pages/slides are marked '--- Seite N ---' / '--- Folie N ---'")
    total_pages: int | None = Field(default=None, description="Pages (PDF) or slides (PowerPoint)")
    pages: str | None = Field(default=None, description="Range contained in text, e.g. '1-12'")
    truncated: bool = False
    next_pages: str | None = Field(default=None, description="Pass as `pages` to continue reading")
    note: str | None = None
