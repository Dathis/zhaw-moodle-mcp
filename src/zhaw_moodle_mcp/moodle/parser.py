"""Pure parsing helpers for Moodle HTML/URLs (no I/O, easy to test)."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

_SESSKEY_RE = re.compile(r'"sesskey":"([^"]+)"')
_USERID_RE = re.compile(r'"userId":(\d+)')
_ICON_RE = re.compile(r"/f/([a-z0-9_]+)(?:-\d+)?(?:\?|$)")
_FOLDER_CONTENT_RE = re.compile(r"/pluginfile\.php/\d+/mod_folder/content/\d+/(.+)$")


@dataclass(frozen=True)
class PageSession:
    logged_in: bool
    user_id: int
    sesskey: str | None


def parse_page_session(page_html: str) -> PageSession:
    """Detect a real (non-guest) login. ZHAW Moodle auto-logs anonymous visitors
    in as guest (userId 1) and then shows a "Log in" link."""
    user_id_m = _USERID_RE.search(page_html)
    user_id = int(user_id_m.group(1)) if user_id_m else 0
    sesskey_m = _SESSKEY_RE.search(page_html)
    soup = BeautifulSoup(page_html, "lxml")
    has_login_link = soup.select_one('a[href$="/login/index.php"]') is not None
    logged_in = user_id > 1 and sesskey_m is not None and not has_login_link
    return PageSession(logged_in, user_id, sesskey_m.group(1) if sesskey_m else None)


def clean_text(value: str | None) -> str:
    """Moodle returns some names HTML-escaped (e.g. `&amp;`)."""
    return " ".join(html.unescape(value or "").split())


# --- resource types ---------------------------------------------------------

_ICON_TYPES = {
    "pdf": "pdf",
    "powerpoint": "powerpoint",
    "impress": "powerpoint",
    "document": "word",
    "writer": "word",
    "spreadsheet": "excel",
    "calc": "excel",
    "archive": "zip",
    "text": "text",
    "sourcecode": "text",
    "markup": "text",
    "image": "image",
    "video": "video",
    "mpeg": "video",
    "audio": "audio",
}

_EXTENSION_TYPES = {
    ".pdf": "pdf",
    ".ppt": "powerpoint", ".pptx": "powerpoint", ".odp": "powerpoint",
    ".doc": "word", ".docx": "word", ".odt": "word", ".rtf": "word",
    ".xls": "excel", ".xlsx": "excel", ".xlsm": "excel", ".ods": "excel", ".csv": "excel",
    ".zip": "zip", ".7z": "zip", ".rar": "zip", ".tar": "zip", ".gz": "zip",
    ".txt": "text", ".md": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".svg": "image",
    ".mp4": "video", ".mov": "video", ".mp3": "audio", ".wav": "audio",
}

MODULE_TYPES = {"folder": "folder", "url": "link", "page": "page"}
DOWNLOADABLE_MODULES = {"resource", "folder"}


def type_from_icon(icon: str | None) -> str:
    return _ICON_TYPES.get(icon or "", "file")


def type_from_filename(filename: str) -> str:
    return _EXTENSION_TYPES.get(PurePosixPath(filename.lower()).suffix, "file")


def parse_activity_icons(course_html: str) -> dict[int, str]:
    """cmid -> file icon name (e.g. 'pdf') from the course page."""
    soup = BeautifulSoup(course_html, "lxml")
    icons: dict[int, str] = {}
    for li in soup.select("li.activity[data-id]"):
        if "modtype_resource" not in li.get("class", []):
            continue
        for img in li.select("img[src]"):
            m = _ICON_RE.search(img["src"])
            if m:
                icons[int(li["data-id"])] = m.group(1)
                break
    return icons


# --- files ------------------------------------------------------------------


@dataclass(frozen=True)
class FolderFile:
    path: str  # relative path inside the folder, e.g. "Slides/week1.pdf"
    url: str  # pluginfile URL without query


def parse_folder_files(folder_html: str, base_url: str) -> list[FolderFile]:
    soup = BeautifulSoup(folder_html, "lxml")
    files: dict[str, FolderFile] = {}
    for a in soup.select("a[href*='/pluginfile.php/']"):
        url = urljoin(base_url + "/", a["href"]).split("?", 1)[0]
        m = _FOLDER_CONTENT_RE.search(urlparse(url).path)
        if m:
            path = unquote(m.group(1))
            files.setdefault(path, FolderFile(path, url))
    return list(files.values())


def filename_from_url(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


_ETAG_ENCODING_SUFFIX = re.compile(r"-(?:gzip|br|deflate|zstd)$")


def normalize_etag(etag: str | None) -> str | None:
    """Apache appends '-gzip' to the ETag of compressed GET responses but not to
    HEAD responses; strip that (and weak/quote markers) so both compare equal."""
    if not etag:
        return None
    value = etag.strip().removeprefix("W/").strip('"')
    return _ETAG_ENCODING_SUFFIX.sub("", value) or None


def is_pluginfile_url(url: str) -> bool:
    return "/pluginfile.php/" in urlparse(url).path


# --- local paths ------------------------------------------------------------

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_component(name: str, max_length: int = 100) -> str:
    """Make a single path component safe on Windows, macOS and Linux."""
    name = unicodedata.normalize("NFC", clean_text(name))
    name = _INVALID_CHARS.sub("_", name)
    name = name.strip(" .")
    if len(name) > max_length:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 8:
            name = stem[: max_length - len(ext) - 1].rstrip(" .") + "." + ext
        else:
            name = name[:max_length].rstrip(" .")
    if not name or name in {".", ".."}:
        name = "_"
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name


def section_dir_name(number: int | None, title: str) -> str:
    return sanitize_component(f"{number:02d} {title}" if number is not None else title, max_length=80)
