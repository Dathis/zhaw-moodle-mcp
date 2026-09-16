from pathlib import Path

import pytest

from zhaw_moodle_mcp.auth.browsers import (
    BUNDLED,
    LaunchTarget,
    exe_from_command,
    find_installed,
    identify,
    mac_default_bundle_id,
    resolve_targets,
)

BRAVE = Path("C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe")
CHROME = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")


def test_exe_from_quoted_command():
    cmd = '"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe" --single-argument %1'
    assert exe_from_command(cmd) == BRAVE


def test_exe_from_unquoted_command():
    assert exe_from_command("C:\\Tools\\vivaldi.exe %1") == Path("C:/Tools/vivaldi.exe")


def test_exe_from_empty_command():
    assert exe_from_command("") is None


@pytest.mark.parametrize(
    ("path", "name"),
    [
        (BRAVE, "brave"),
        (CHROME, "chrome"),
        (EDGE, "msedge"),
        (Path("C:/Users/x/AppData/Local/Vivaldi/Application/vivaldi.exe"), "vivaldi"),
        (Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"), "msedge"),
        (Path("/Applications/Chromium.app/Contents/MacOS/Chromium"), "chromium"),
        (Path("/usr/bin/google-chrome-stable"), "chrome"),
    ],
)
def test_identify_chromium_browsers(path, name):
    assert identify(path) == name


@pytest.mark.parametrize(
    "path",
    [Path("C:/Program Files/Mozilla Firefox/firefox.exe"), Path("/Applications/Safari.app/Contents/MacOS/Safari")],
)
def test_identify_unsupported_browsers(path):
    assert identify(path) is None


def test_auto_prefers_default_browser():
    targets = resolve_targets("auto", None, default=BRAVE, installed={"chrome": CHROME, "msedge": EDGE})
    assert targets[0] == LaunchTarget("brave", BRAVE)
    assert [t.name for t in targets] == ["brave", "chrome", "msedge", BUNDLED.name]


def test_auto_skips_unsupported_default():
    firefox = Path("C:/Program Files/Mozilla Firefox/firefox.exe")
    targets = resolve_targets("auto", None, default=firefox, installed={"msedge": EDGE})
    assert [t.name for t in targets] == ["msedge", BUNDLED.name]


def test_auto_does_not_repeat_default():
    targets = resolve_targets("auto", None, default=EDGE, installed={"chrome": CHROME, "msedge": EDGE})
    assert [t.name for t in targets] == ["msedge", "chrome", BUNDLED.name]


def test_auto_without_any_browser_uses_bundled():
    assert resolve_targets("auto", None, default=None, installed={}) == [BUNDLED]


def test_named_browser():
    targets = resolve_targets("brave", None, default=EDGE, installed={"brave": BRAVE, "msedge": EDGE})
    assert targets == [LaunchTarget("brave", BRAVE)]


def test_named_browser_not_installed():
    assert resolve_targets("vivaldi", None, default=None, installed={}) == []


def test_chromium_means_bundled():
    assert resolve_targets("chromium", None, default=None, installed={}) == [BUNDLED]


def test_executable_path_wins():
    custom = Path("D:/Portable/Thorium/thorium.exe")
    targets = resolve_targets("auto", custom, default=BRAVE, installed={"chrome": CHROME})
    assert targets == [LaunchTarget("custom", custom)]


def test_find_installed_windows(tmp_path):
    local = tmp_path / "Local"
    brave = local / "BraveSoftware/Brave-Browser/Application/brave.exe"
    brave.parent.mkdir(parents=True)
    brave.touch()
    env = {"LOCALAPPDATA": str(local), "PROGRAMFILES": str(tmp_path / "PF")}
    assert find_installed("win32", env) == {"brave": brave}


def test_find_installed_macos(tmp_path):
    app = tmp_path / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    app.parent.mkdir(parents=True)
    app.touch()
    assert find_installed("darwin", {}, roots=[tmp_path / "Applications"]) == {"chrome": app}


def test_mac_default_bundle_id():
    plist = {
        "LSHandlers": [
            {"LSHandlerURLScheme": "mailto", "LSHandlerRoleAll": "com.apple.mail"},
            {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.brave.browser"},
        ]
    }
    assert mac_default_bundle_id(plist) == "com.brave.browser"


def test_mac_default_bundle_id_missing():
    assert mac_default_bundle_id({}) is None
