from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError

from zhaw_moodle_mcp.auth.browser import launch_first
from zhaw_moodle_mcp.auth.browsers import BUNDLED, LaunchTarget
from zhaw_moodle_mcp.errors import MoodleError


class FakeChromium:
    def __init__(self, broken: set[str]) -> None:
        self.broken = broken
        self.calls: list[dict] = []

    async def launch_persistent_context(self, **kwargs):
        self.calls.append(kwargs)
        if (kwargs["executable_path"] or "bundled") in self.broken:
            raise PlaywrightError("cannot start")
        return "context"


BRAVE = LaunchTarget("brave", Path("C:/Brave/brave.exe"))
EDGE = LaunchTarget("msedge", Path("C:/Edge/msedge.exe"))


async def test_uses_first_browser_that_starts(tmp_path):
    chromium = FakeChromium(broken={str(BRAVE.executable)})
    context, target = await launch_first(chromium, [BRAVE, EDGE], tmp_path)
    assert (context, target) == ("context", EDGE)
    assert [c["executable_path"] for c in chromium.calls] == [str(BRAVE.executable), str(EDGE.executable)]


async def test_each_browser_gets_its_own_profile(tmp_path):
    chromium = FakeChromium(broken=set())
    await launch_first(chromium, [BRAVE], tmp_path)
    assert chromium.calls[0]["user_data_dir"] == str(tmp_path / "brave")
    assert chromium.calls[0]["headless"] is False


async def test_bundled_chromium_has_no_executable(tmp_path):
    chromium = FakeChromium(broken=set())
    await launch_first(chromium, [BUNDLED], tmp_path)
    assert chromium.calls[0]["executable_path"] is None


async def test_no_browser_starts(tmp_path):
    chromium = FakeChromium(broken={str(BRAVE.executable), "bundled"})
    with pytest.raises(MoodleError, match="playwright install chromium"):
        await launch_first(chromium, [BRAVE, BUNDLED], tmp_path)


async def test_no_candidates(tmp_path):
    with pytest.raises(MoodleError, match="No supported browser"):
        await launch_first(FakeChromium(broken=set()), [], tmp_path)
