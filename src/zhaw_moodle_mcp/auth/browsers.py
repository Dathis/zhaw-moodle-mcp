"""Choose the browser for the interactive login.

Playwright can drive any installed Chromium-based browser (Chrome, Edge, Brave,
Vivaldi) via its executable, but not installed Firefox or Safari. With "auto" the
user's default browser is used if it is Chromium-based, otherwise the first
installed one, and Playwright's bundled Chromium as the last resort.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LaunchTarget:
    name: str
    # None = Playwright's bundled Chromium
    executable: Path | None


BUNDLED = LaunchTarget("chromium", None)

# Order = preference when the default browser can't be used
KNOWN_BROWSERS = ("chrome", "msedge", "brave", "vivaldi")

# Checked against the executable / app name, most specific first
_NAME_KEYWORDS = (
    ("vivaldi", "vivaldi"),
    ("brave", "brave"),
    ("edge", "msedge"),
    ("chromium", "chromium"),
    ("chrome", "chrome"),
)

_WINDOWS_PATHS = {
    "chrome": [("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
               ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
               ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe")],
    "msedge": [("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
               ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
               ("LOCALAPPDATA", "Microsoft/Edge/Application/msedge.exe")],
    "brave": [("PROGRAMFILES", "BraveSoftware/Brave-Browser/Application/brave.exe"),
              ("PROGRAMFILES(X86)", "BraveSoftware/Brave-Browser/Application/brave.exe"),
              ("LOCALAPPDATA", "BraveSoftware/Brave-Browser/Application/brave.exe")],
    "vivaldi": [("LOCALAPPDATA", "Vivaldi/Application/vivaldi.exe"),
                ("PROGRAMFILES", "Vivaldi/Application/vivaldi.exe")],
}

_MAC_APPS = {
    "chrome": "Google Chrome.app/Contents/MacOS/Google Chrome",
    "msedge": "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "brave": "Brave Browser.app/Contents/MacOS/Brave Browser",
    "vivaldi": "Vivaldi.app/Contents/MacOS/Vivaldi",
}

_MAC_BUNDLE_IDS = {
    "com.google.chrome": "chrome",
    "com.microsoft.edgemac": "msedge",
    "com.brave.browser": "brave",
    "com.vivaldi.vivaldi": "vivaldi",
}

_LINUX_COMMANDS = {
    "chrome": ["google-chrome", "google-chrome-stable"],
    "msedge": ["microsoft-edge", "microsoft-edge-stable"],
    "brave": ["brave-browser", "brave"],
    "vivaldi": ["vivaldi", "vivaldi-stable"],
}


def exe_from_command(command: str) -> Path | None:
    """Executable of a Windows shell open command like `"C:\\...\\brave.exe" --single-argument %1`."""
    command = command.strip()
    if not command:
        return None
    if command.startswith('"'):
        exe = command[1:].split('"', 1)[0]
    else:
        exe = command.split(" ", 1)[0]
    return Path(exe.replace("\\", "/"))


def identify(path: Path) -> str | None:
    """Browser name for a Chromium-based executable, None for anything else."""
    names = [path.name] + [part for part in path.parts if part.endswith(".app")]
    text = " ".join(names).lower()
    for keyword, name in _NAME_KEYWORDS:
        if keyword in text:
            return name
    return None


def mac_default_bundle_id(launch_services: Mapping) -> str | None:
    for handler in launch_services.get("LSHandlers", []):
        if handler.get("LSHandlerURLScheme") == "https":
            return handler.get("LSHandlerRoleAll")
    return None


def find_installed(
    platform: str = sys.platform,
    env: Mapping[str, str] = os.environ,
    roots: list[Path] | None = None,
) -> dict[str, Path]:
    """Installed Chromium-based browsers in preference order."""
    found: dict[str, Path] = {}
    for name in KNOWN_BROWSERS:
        if platform == "win32":
            candidates = [Path(env[var]) / rel for var, rel in _WINDOWS_PATHS[name] if env.get(var)]
        elif platform == "darwin":
            roots = roots if roots is not None else [Path("/Applications"), Path.home() / "Applications"]
            candidates = [root / _MAC_APPS[name] for root in roots]
        else:
            candidates = [Path(p) for cmd in _LINUX_COMMANDS[name] if (p := shutil.which(cmd))]
        exe = next((c for c in candidates if c.is_file()), None)
        if exe:
            found[name] = exe
    return found


def detect_default_browser(installed: Mapping[str, Path], platform: str = sys.platform) -> Path | None:
    """Executable of the user's default web browser (best effort)."""
    try:
        if platform == "win32":
            import winreg

            key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                prog_id = winreg.QueryValueEx(k, "ProgId")[0]
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as k:
                return exe_from_command(winreg.QueryValue(k, None))
        if platform == "darwin":
            plist = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
            bundle_id = mac_default_bundle_id(plistlib.loads(plist.read_bytes()))
            name = _MAC_BUNDLE_IDS.get((bundle_id or "").lower())
            return installed.get(name) if name else None
        result = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, check=True
        )
        name = identify(Path(result.stdout.strip()))
        return installed.get(name) if name else None
    except (OSError, ValueError, subprocess.SubprocessError, plistlib.InvalidFileException):
        log.debug("Could not determine the default browser", exc_info=True)
        return None


def resolve_targets(
    setting: str,
    executable_path: Path | None,
    *,
    default: Path | None,
    installed: Mapping[str, Path],
) -> list[LaunchTarget]:
    """Browsers to try for the login, in order."""
    if executable_path:
        return [LaunchTarget("custom", executable_path)]
    if setting == BUNDLED.name:
        return [BUNDLED]
    if setting != "auto":
        return [LaunchTarget(setting, installed[setting])] if setting in installed else []

    targets: list[LaunchTarget] = []
    default_name = identify(default) if default else None
    if default and default_name:
        targets.append(LaunchTarget(default_name, default))
    elif default:
        log.info("Default browser %s is not Chromium-based; using another browser for login", default.name)
    targets += [LaunchTarget(name, exe) for name, exe in installed.items() if name != default_name]
    targets.append(BUNDLED)
    return targets


def login_targets(setting: str, executable_path: Path | None) -> list[LaunchTarget]:
    installed = find_installed()
    default = None if executable_path or setting != "auto" else detect_default_browser(installed)
    return resolve_targets(setting, executable_path, default=default, installed=installed)
