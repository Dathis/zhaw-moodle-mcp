"""Interactive login: the user signs in via SWITCH edu-ID in a visible browser.

The only place that uses Playwright. The password never passes through this code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from ..config import BrowserConfig
from ..errors import ErrorCode, MoodleError
from .session import SessionStore

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 1.0

# ZHAW Moodle auto-logs anonymous visitors in as guest (userId 1, no `notloggedin`
# body class), so require a real user id and no "Log in" link on the page.
IS_LOGGED_IN_JS = """
() => !!(window.M && M.cfg && M.cfg.sesskey)
      && M.cfg.userId > 1
      && !document.querySelector('a[href$="/login/index.php"]')
"""


async def interactive_login(base_url: str, browser: BrowserConfig, store: SessionStore) -> None:
    """Open a headed browser, wait until Moodle shows an authenticated page and
    persist the browser state. Raises MoodleError(LOGIN_FAILED) on timeout/close."""
    store.prepare()
    moodle_host = urlparse(base_url).hostname
    log.info("Opening login browser (%s)", browser.channel)

    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(browser.profile_directory),
                channel=None if browser.channel == "chromium" else browser.channel,
                headless=False,
            )
        except PlaywrightError as exc:
            hint = (
                "run `uv run playwright install chromium`"
                if browser.channel == "chromium"
                else f"is {browser.channel} installed? Or set [browser] channel = \"chromium\""
            )
            raise MoodleError(ErrorCode.LOGIN_FAILED, f"Could not start the browser ({hint}).") from exc

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(f"{base_url}/login/index.php")

            deadline = time.monotonic() + browser.login_timeout
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise MoodleError(ErrorCode.LOGIN_FAILED, "The login browser was closed before login finished.")
                if urlparse(page.url).hostname == moodle_host and not urlparse(page.url).path.startswith("/login"):
                    try:
                        if await page.evaluate(IS_LOGGED_IN_JS):
                            break
                    except PlaywrightError:
                        pass  # page is mid-navigation
                await asyncio.sleep(POLL_INTERVAL_S)
            else:
                raise MoodleError(
                    ErrorCode.LOGIN_FAILED,
                    f"Login was not completed within {browser.login_timeout} seconds.",
                )

            await context.storage_state(path=str(store.storage_state))
            store.secure()
            log.info("Login detected, session state stored")
        except PlaywrightError as exc:
            raise MoodleError(ErrorCode.LOGIN_FAILED, "The login browser failed or was closed.") from exc
        finally:
            try:
                await context.close()
            except PlaywrightError:
                pass
