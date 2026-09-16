"""Text content of Moodle pages (mod_page) and labels ("Textfeld") as Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString, Tag

from .dates import parse_moodle_date
from .parser import parse_activity_icons

LinkKind = Literal["file", "activity", "moodle", "external"]

_BLOCKS = {"p", "div", "section", "article", "blockquote", "pre", "figure", "header", "footer"}
_SKIP = {"script", "style", "noscript", "template", "button", "input", "select", "form", "iframe"}


@dataclass(frozen=True)
class ContentLink:
    text: str
    url: str
    kind: LinkKind
    activity_id: int | None = None


_PLUGINFILE_RE = re.compile(r"/pluginfile\.php/\d+/[a-z0-9_]+/[a-z0-9_]+/(.+)$")


def embedded_file_path(url: str) -> str | None:
    """Stable path of a file linked in a text, e.g. 'plan.pdf' for
    /pluginfile.php/<ctx>/mod_page/content/<revision>/plan.pdf (the revision changes on edits)."""
    m = _PLUGINFILE_RE.search(urlparse(url).path)
    if not m:
        return None
    parts = [unquote(p) for p in m.group(1).split("/") if p]
    if len(parts) > 1 and parts[0].isdigit():
        parts = parts[1:]  # item id / revision
    if not parts or any(p in {".", ".."} for p in parts):
        return None
    return "/".join(parts)


def _link_kind(url: str, moodle_host: str) -> tuple[LinkKind, int | None]:
    parsed = urlparse(url)
    if parsed.hostname != moodle_host:
        return "external", None
    if "/pluginfile.php/" in parsed.path:
        return "file", None
    m = re.search(r"/mod/\w+/view\.php", parsed.path)
    cmid = re.search(r"(?:^|&)id=(\d+)", parsed.query)
    if m and cmid:
        return "activity", int(cmid.group(1))
    return "moodle", None


class _Markdown:
    """Small HTML -> Markdown converter for course texts."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.moodle_host = urlparse(base_url).hostname or ""
        self.links: list[ContentLink] = []

    def convert(self, node: Tag) -> str:
        text = self._children(node)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _children(self, node: Tag) -> str:
        return "".join(self._node(child) for child in node.children)

    def _inline(self, node: Tag) -> str:
        return " ".join(self._children(node).split())

    def _node(self, node: object) -> str:
        if isinstance(node, NavigableString):
            if node.__class__.__name__ in {"Comment", "Doctype", "CData", "ProcessingInstruction"}:
                return ""
            return re.sub(r"\s+", " ", str(node))
        if not isinstance(node, Tag):
            return ""
        name = node.name
        classes = node.get("class", [])
        if name in _SKIP or "visually-hidden" in classes or "sr-only" in classes:
            return ""
        if name == "br":
            return "\n"
        if name == "hr":
            return "\n\n---\n\n"
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = min(int(name[1]) + 1, 6)  # the activity name is the top heading
            text = self._inline(node)
            return f"\n\n{'#' * level} {text}\n\n" if text else ""
        if name in {"strong", "b"}:
            text = self._inline(node)
            return f"**{text}**" if text else ""
        if name in {"em", "i"}:
            text = self._inline(node)
            return f"*{text}*" if text else ""
        if name == "code" and node.find_parent("pre") is None:
            return f"`{node.get_text()}`"
        if name == "pre":
            return f"\n\n```\n{node.get_text().strip()}\n```\n\n"
        if name == "a":
            return self._link(node)
        if name == "img":
            alt = " ".join((node.get("alt") or "").split())
            return f"[Bild: {alt}]" if alt else ""
        if name in {"ul", "ol"}:
            return self._list(node)
        if name == "table":
            return self._table(node)
        if name in _BLOCKS:
            inner = self._children(node).strip()
            return f"\n\n{inner}\n\n" if inner else ""
        return self._children(node)

    def _link(self, node: Tag) -> str:
        text = self._inline(node)
        href = node.get("href")
        if not href or href.startswith(("#", "javascript:")):
            return text
        url = urljoin(self.base_url + "/", href)
        if url.startswith("mailto:"):
            return f"{text} ({url[7:]})" if text and text != url[7:] else url[7:]
        kind, cmid = _link_kind(url, self.moodle_host)
        label = text or url
        if not any(link.url == url for link in self.links):
            self.links.append(ContentLink(text=label, url=url, kind=kind, activity_id=cmid))
        return f"[{label}]({url})"

    def _list(self, node: Tag, depth: int = 0) -> str:
        lines = []
        ordered = node.name == "ol"
        for i, li in enumerate(node.find_all("li", recursive=False), start=1):
            nested = [child for child in li.find_all(["ul", "ol"], recursive=False)]
            for child in nested:
                child.extract()
            text = self._inline(li)
            marker = f"{i}." if ordered else "-"
            lines.append(f"{'  ' * depth}{marker} {text}")
            for child in nested:
                lines.append(self._list(child, depth + 1).strip("\n"))
        return "\n\n" + "\n".join(lines) + "\n\n" if lines else ""

    def _table(self, node: Tag) -> str:
        rows = []
        for tr in node.find_all("tr"):
            cells = [self._inline(cell).replace("|", "\\|") for cell in tr.find_all(["th", "td"], recursive=False)]
            if any(cells):
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n\n" + "\n".join(lines) + "\n\n"


@dataclass
class Content:
    text: str
    links: list[ContentLink] = field(default_factory=list)
    modified_at: datetime | None = None
    modified_text: str | None = None


def to_markdown(node: Tag, base_url: str) -> Content:
    converter = _Markdown(base_url)
    return Content(text=converter.convert(node), links=converter.links)


@dataclass
class CoursePage:
    icons: dict[int, str]
    labels: dict[int, Content]


def parse_course_page(course_html: str, base_url: str) -> CoursePage:
    """File-type icons and label texts from the course page."""
    soup = BeautifulSoup(course_html, "lxml")
    labels = {}
    for li in soup.select("li.activity.modtype_label[data-id]"):
        body = li.select_one(".activity-altcontent, .contentwithoutlink, .description")
        if body is not None and str(li["data-id"]).isdigit():
            labels[int(li["data-id"])] = to_markdown(body, base_url)
    return CoursePage(icons=parse_activity_icons(course_html), labels=labels)


def parse_page_view(page_html: str, base_url: str, tz: ZoneInfo) -> Content | None:
    """Content of mod/page/view.php; None if the page has no content box."""
    soup = BeautifulSoup(page_html, "lxml")
    main = soup.select_one("[role='main']") or soup
    box = main.select_one(".generalbox")
    if box is None:
        return None
    content = to_markdown(box, base_url)
    intro = main.select_one(".activity-description")
    if intro is not None:
        intro_content = to_markdown(intro, base_url)
        if intro_content.text:
            content.text = f"{intro_content.text}\n\n---\n\n{content.text}".strip()
            content.links = intro_content.links + [
                link for link in content.links if link.url not in {i.url for i in intro_content.links}]
    modified = main.select_one(".modified")
    if modified is not None:
        content.modified_text = " ".join(modified.get_text(" ").split()) or None
        if content.modified_text:
            content.modified_at = parse_moodle_date(content.modified_text, tz)
    return content
