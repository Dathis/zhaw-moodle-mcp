from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HitKind = Literal["course", "section", "module", "file"]


class SearchHit(BaseModel):
    kind: HitKind
    id: str = Field(description="course id, section id, cmid, or '<folder cmid>/<path>' for folder files")
    name: str
    course_id: int
    course_name: str
    section: str | None = None
    type: str | None = Field(default=None, description="pdf, folder, link, page, assign, quiz, ...")
    url: str | None = None
    downloadable: bool = False
    score: float
    snippet: str | None = None


class SearchResult(BaseModel):
    query: str
    source: Literal["index", "moodle"]
    hits: list[SearchHit]
    indexed_courses: int = 0
    note: str | None = None
