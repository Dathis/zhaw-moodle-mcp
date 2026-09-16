"""Local persistence of the authenticated browser state (outside the repository)."""

from __future__ import annotations

import getpass
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


def restrict_permissions(path: Path) -> None:
    """Make a file/directory accessible to the current user only (best effort)."""
    try:
        if sys.platform == "win32":
            grant = "(OI)(CI)F" if path.is_dir() else "F"
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{getpass.getuser()}:{grant}"],
                check=True,
                capture_output=True,
            )
        else:
            path.chmod(0o700 if path.is_dir() else 0o600)
    except (OSError, subprocess.CalledProcessError) as exc:
        log.warning("Could not restrict permissions on %s: %s", path.name, type(exc).__name__)


def ensure_private_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True)
        restrict_permissions(path)


def _domain_matches(host: str, cookie_domain: str) -> bool:
    domain = cookie_domain.lstrip(".")
    return host == domain or host.endswith("." + domain)


class SessionStore:
    def __init__(self, storage_state: Path, profile_dir: Path) -> None:
        self.storage_state = storage_state
        self.profile_dir = profile_dir

    def exists(self) -> bool:
        return self.storage_state.is_file()

    def prepare(self) -> None:
        ensure_private_dir(self.storage_state.parent)
        ensure_private_dir(self.profile_dir)

    def secure(self) -> None:
        if self.storage_state.exists():
            restrict_permissions(self.storage_state)

    def load_cookies(self, host: str) -> httpx.Cookies:
        """Cookies from the stored browser state that apply to `host`."""
        jar = httpx.Cookies()
        if not self.exists():
            return jar
        try:
            state = json.loads(self.storage_state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Stored session state is unreadable; ignoring it")
            return jar
        for c in state.get("cookies", []):
            if _domain_matches(host, c.get("domain", "")):
                jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
        return jar

    def clear(self) -> None:
        """Delete the stored state and the browser profile (incl. SSO cookies)."""
        if self.storage_state.exists():
            os.remove(self.storage_state)
        if self.profile_dir.exists():
            shutil.rmtree(self.profile_dir, ignore_errors=True)
        log.info("Local authentication state removed")
