"""Flatten course structure into resources, expand folders, compute local paths."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..models import CourseModule, CourseSection, CourseStructure, Resource
from .client import MoodleClient
from .parser import (
    FolderFile,
    filename_from_url,
    parse_folder_files,
    sanitize_component,
    section_dir_name,
    type_from_filename,
)

LISTED_MODULES = {"resource", "folder", "url", "page"}


@dataclass
class ResourceEntry:
    """A resource plus the internal data needed to download it."""

    resource: Resource
    section_dirs: list[str]
    folder_dirs: list[str] = field(default_factory=list)  # for files inside a folder
    file_url: str | None = None  # pluginfile URL if already known

    @property
    def id(self) -> str:
        return self.resource.id

    @property
    def is_file(self) -> bool:
        """A single downloadable file (not a folder container, link or page)."""
        return self.resource.downloadable and (
            self.resource.module == "resource" or self.resource.folder_id is not None
        )

    def default_path(self, download_root: Path, course_name: str, filename: str) -> Path:
        return download_root.joinpath(
            sanitize_component(course_name), *self.section_dirs, *self.folder_dirs, sanitize_component(filename)
        )


def _walk(sections: list[CourseSection], titles: list[str], dirs: list[str]
          ) -> Iterator[tuple[CourseModule, list[str], list[str]]]:
    for s in sections:
        s_titles = [*titles, s.title]
        # top-level sections get their number as prefix to keep Moodle's order
        s_dirs = [*dirs, section_dir_name(None if dirs else s.number, s.title)]
        for m in s.modules:
            yield m, s_titles, s_dirs
        yield from _walk(s.subsections, s_titles, s_dirs)


def module_dirs(structure: CourseStructure) -> dict[int, list[str]]:
    """Local directory components (below the course directory) of every module's section."""
    return {module.id: dirs for module, _, dirs in _walk(structure.sections, [], [])}


def entries_from_structure(structure: CourseStructure) -> list[ResourceEntry]:
    entries = []
    for module, titles, dirs in _walk(structure.sections, [], []):
        if module.module not in LISTED_MODULES:
            continue
        entries.append(ResourceEntry(
            resource=Resource(
                id=str(module.id),
                course_id=structure.course.id,
                section=" / ".join(titles),
                name=module.name,
                type=module.type,
                module=module.module,
                url=module.url or "",
                downloadable=module.downloadable,
            ),
            section_dirs=dirs,
        ))
    return entries


def folder_file_entries(folder: ResourceEntry, files: list[FolderFile]) -> list[ResourceEntry]:
    result = []
    for f in files:
        parts = f.path.split("/")
        result.append(ResourceEntry(
            resource=Resource(
                id=f"{folder.resource.id}/{f.path}",
                course_id=folder.resource.course_id,
                section=folder.resource.section,
                name=f.path,
                type=type_from_filename(f.path),
                module="folder",
                url=f.url,
                downloadable=True,
                folder_id=int(folder.resource.id),
                filename=filename_from_url(f.url),
            ),
            section_dirs=folder.section_dirs,
            folder_dirs=[sanitize_component(folder.resource.name), *map(sanitize_component, parts[:-1])],
            file_url=f.url,
        ))
    return result


async def expand_folders(client: MoodleClient, entries: list[ResourceEntry],
                         only: set[int] | None = None) -> list[ResourceEntry]:
    """Insert the files of folder modules right after each folder entry."""
    folders = [e for e in entries
               if e.resource.module == "folder" and e.resource.folder_id is None and e.resource.downloadable
               and (only is None or int(e.id) in only)]

    async def files_of(folder: ResourceEntry) -> list[ResourceEntry]:
        html = await client.get_html("/mod/folder/view.php", {"id": folder.id})
        return folder_file_entries(folder, parse_folder_files(html, client.base_url))

    expanded = dict(zip((f.id for f in folders),
                        await asyncio.gather(*(files_of(f) for f in folders)), strict=True))
    result = []
    for e in entries:
        result.append(e)
        result.extend(expanded.get(e.id, []))
    return result
