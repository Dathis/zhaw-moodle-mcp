"""Low-level HTTP access to Moodle using the cookies of the browser login.

Knows about Moodle endpoints, sessions and retries; knows nothing about MCP.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urljoin, urlparse

import httpx

from .. import __version__
from ..auth.session import SessionStore
from ..config import MoodleConfig
from ..errors import ErrorCode, MoodleError, SessionExpired
from .parser import is_pluginfile_url, normalize_etag, parse_page_session

log = logging.getLogger(__name__)

T = TypeVar("T")

RETRY_STATUSES = {502, 503, 504}
RETRY_ATTEMPTS = 3
SESSION_ERRORCODES = {"servicerequireslogin", "invalidsesskey", "sessiontimedout"}


class MoodleAjaxError(MoodleError):
    def __init__(self, errorcode: str, message: str) -> None:
        super().__init__(ErrorCode.MOODLE_ERROR, f"{errorcode}: {message}")
        self.errorcode = errorcode


@dataclass(frozen=True)
class FileProbe:
    status: int  # 200, 404, ...
    etag: str | None
    last_modified: str | None
    size: int | None


@dataclass(frozen=True)
class DownloadInfo:
    path: Path
    size: int
    sha256: str
    etag: str | None
    last_modified: str | None


class MoodleClient:
    def __init__(self, config: MoodleConfig, store: SessionStore) -> None:
        self.config = config
        self.store = store
        self.base_url = config.base_url
        self.host = urlparse(config.base_url).hostname or ""
        self._http: httpx.AsyncClient | None = None
        self._sesskey: str | None = None
        self._semaphore = asyncio.Semaphore(config.max_concurrent_requests)

    # --- lifecycle ----------------------------------------------------------

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                cookies=self.store.load_cookies(self.host),
                timeout=self.config.request_timeout,
                headers={"User-Agent": f"zhaw-moodle-mcp/{__version__}"},
            )
        return self._http

    async def reset(self) -> None:
        """Drop the HTTP client so the next request reloads stored cookies."""
        if self._http is not None:
            await self._http.aclose()
        self._http = None
        self._sesskey = None

    async def aclose(self) -> None:
        await self.reset()

    # --- plumbing -----------------------------------------------------------

    async def _with_retry(self, what: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Retry transport errors and 502/503/504. Only for read-like requests."""
        delay = 1.0
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                async with self._semaphore:
                    return await fn()
            except httpx.TransportError as exc:
                reason = type(exc).__name__
            except _RetryableStatus as exc:
                reason = f"HTTP {exc.status}"
            if attempt == RETRY_ATTEMPTS:
                log.warning("%s failed after %d attempts (%s)", what, attempt, reason)
                raise MoodleError(ErrorCode.MOODLE_UNAVAILABLE, f"Moodle is not reachable ({reason}).")
            log.info("%s: %s, retrying in %.0fs", what, reason, delay)
            await asyncio.sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def send() -> httpx.Response:
            r = await self.http.request(method, url, **kwargs)
            if r.status_code in RETRY_STATUSES:
                raise _RetryableStatus(r.status_code)
            return r

        return await self._with_retry(f"{method} {urlparse(url).path}", send)

    def _absolute(self, url: str) -> str:
        return urljoin(self.base_url + "/", url)

    def _is_login_redirect(self, r: httpx.Response) -> bool:
        location = r.headers.get("location", "")
        return r.is_redirect and "/login/" in urlparse(self._absolute(location)).path

    # --- session ------------------------------------------------------------

    async def check_session(self) -> bool:
        """True if the stored cookies belong to a logged-in (non-guest) user."""
        if not self.store.exists():
            return False
        r = await self._request("GET", "/my/", follow_redirects=True)
        info = parse_page_session(r.text) if r.status_code == 200 else None
        valid = bool(info and info.logged_in and urlparse(str(r.url)).hostname == self.host)
        self._sesskey = info.sesskey if valid and info else None
        log.info("Moodle session %s", "valid" if valid else "invalid")
        return valid

    async def sesskey(self) -> str:
        if self._sesskey is None and not await self.check_session():
            raise SessionExpired()
        assert self._sesskey is not None
        return self._sesskey

    async def logout(self) -> bool:
        """Invalidate the server-side Moodle session. Returns True on success."""
        if self._sesskey is None:
            return False
        r = await self.http.get("/login/logout.php", params={"sesskey": self._sesskey})
        return r.status_code in (200, 303)

    # --- data access --------------------------------------------------------

    async def ajax(self, method: str, args: dict[str, Any]) -> Any:
        """Call a read-only function of Moodle's session-authenticated AJAX service."""
        sesskey = await self.sesskey()
        r = await self._request(
            "POST",
            "/lib/ajax/service.php",
            params={"sesskey": sesskey, "info": method},
            json=[{"index": 0, "methodname": method, "args": args}],
        )
        try:
            body = r.json()
        except ValueError as exc:
            raise MoodleError(ErrorCode.MOODLE_ERROR, f"Unexpected response from {method}.") from exc
        if isinstance(body, dict):  # request-level failure
            code = str(body.get("errorcode", ""))
            if code in SESSION_ERRORCODES:
                self._sesskey = None
                raise SessionExpired()
            raise MoodleAjaxError(code, str(body.get("error", "")))
        item = body[0]
        if item.get("error"):
            exc_info = item.get("exception") or {}
            code = str(exc_info.get("errorcode", ""))
            if code in SESSION_ERRORCODES:
                self._sesskey = None
                raise SessionExpired()
            raise MoodleAjaxError(code, str(exc_info.get("message", "")))
        return item["data"]

    async def get_html(self, path: str, params: dict[str, Any] | None = None) -> str:
        r = await self._request("GET", path, params=params, follow_redirects=False)
        if self._is_login_redirect(r):
            raise SessionExpired()
        if r.is_redirect:
            r = await self._request("GET", self._absolute(r.headers["location"]), follow_redirects=True)
        if r.status_code == 404:
            raise MoodleError(ErrorCode.RESOURCE_NOT_FOUND, f"{path} not found.")
        if r.status_code != 200:
            raise MoodleError(ErrorCode.MOODLE_ERROR, f"{path} returned HTTP {r.status_code}.")
        if not parse_page_session(r.text).logged_in:
            raise SessionExpired()
        return r.text

    async def resolve_resource_file(self, cmid: int) -> str:
        """pluginfile URL of a mod_resource (one view request, no download)."""
        r = await self._request(
            "GET", "/mod/resource/view.php", params={"id": cmid, "redirect": 1}, follow_redirects=False
        )
        if self._is_login_redirect(r):
            raise SessionExpired()
        location = self._absolute(r.headers.get("location", "")) if r.is_redirect else ""
        if is_pluginfile_url(location):
            return location.split("?", 1)[0]
        if r.status_code == 200 and not parse_page_session(r.text).logged_in:
            raise SessionExpired()
        if r.status_code == 404:
            raise MoodleError(ErrorCode.RESOURCE_NOT_FOUND, f"Resource {cmid} not found.")
        raise MoodleError(ErrorCode.DOWNLOAD_FAILED, f"Resource {cmid} does not point to a downloadable file.")

    async def _unavailable_file_status(self, r: httpx.Response) -> int:
        """Status for a pluginfile response that is not the file. Course files may
        themselves be HTML, so only redirects hint at a lost session."""
        if self._is_login_redirect(r):
            raise SessionExpired()
        if r.is_redirect:
            # e.g. redirect to the enrolment page: session lost or access revoked
            if not await self.check_session():
                raise SessionExpired()
            return 403
        return r.status_code

    async def probe_file(self, url: str) -> FileProbe:
        """HEAD a pluginfile URL to detect changes without downloading."""
        r = await self._request("HEAD", url, follow_redirects=False)
        if r.is_redirect or r.status_code != 200:
            return FileProbe(await self._unavailable_file_status(r), None, None, None)
        length = r.headers.get("content-length")
        return FileProbe(r.status_code, normalize_etag(r.headers.get("etag")), r.headers.get("last-modified"),
                         int(length) if length and length.isdigit() else None)

    async def download(self, url: str, target: Path) -> DownloadInfo:
        """Stream a pluginfile URL to `target` atomically."""
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")

        async def fetch() -> DownloadInfo | httpx.Response:
            async with self.http.stream("GET", url, follow_redirects=False) as r:
                if r.status_code in RETRY_STATUSES:
                    raise _RetryableStatus(r.status_code)
                if r.status_code != 200:
                    return r  # evaluated outside the semaphore (may re-check the session)
                log.info("Downloading %s", target.name)
                digest = hashlib.sha256()
                size = 0
                try:
                    with part.open("wb") as f:
                        async for chunk in r.aiter_bytes(1 << 16):
                            f.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    os.replace(part, target)
                except OSError as exc:
                    part.unlink(missing_ok=True)
                    raise MoodleError(ErrorCode.DOWNLOAD_FAILED, f"Cannot write file: {exc.strerror}.") from exc
                except BaseException:
                    part.unlink(missing_ok=True)
                    raise
                return DownloadInfo(target, size, digest.hexdigest(),
                                    normalize_etag(r.headers.get("etag")), r.headers.get("last-modified"))

        result = await self._with_retry("download", fetch)
        if isinstance(result, DownloadInfo):
            return result
        status = await self._unavailable_file_status(result)
        if status in (403, 404, 410):
            raise MoodleError(ErrorCode.RESOURCE_NOT_FOUND, "File is no longer available.")
        raise MoodleError(ErrorCode.DOWNLOAD_FAILED, f"HTTP {status}.")


class _RetryableStatus(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
