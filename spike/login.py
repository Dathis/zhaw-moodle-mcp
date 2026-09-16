"""Spike: open a headed browser, wait for the user to log in via SWITCH edu-ID,
detect success automatically and export Playwright storage_state."""

import asyncio
import sys
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from common import BASE_URL, PROFILE_DIR, STORAGE_STATE

LOGIN_TIMEOUT_S = 600
POLL_INTERVAL_S = 2

# ZHAW Moodle auto-logs anonymous visitors in as guest (userId 1, no `notloggedin`
# body class), so require a real user id and no "Log in" link on the page.
IS_LOGGED_IN_JS = """
() => !!(window.M && M.cfg && M.cfg.sesskey)
      && M.cfg.userId > 1
      && !document.querySelector('a[href$="/login/index.php"]')
"""


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_STATE.parent.mkdir(parents=True, exist_ok=True)
    moodle_host = urlparse(BASE_URL).hostname

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(f"{BASE_URL}/login/index.php")
        print("Browser opened, waiting for login...", flush=True)

        start = time.monotonic()
        while time.monotonic() - start < LOGIN_TIMEOUT_S:
            url = urlparse(page.url)
            if url.hostname == moodle_host and not url.path.startswith("/login"):
                try:
                    if await page.evaluate(IS_LOGGED_IN_JS):
                        break
                except Exception:
                    pass  # page is mid-navigation
            else:
                print(f"  on {url.hostname}{url.path}", flush=True)
            await asyncio.sleep(POLL_INTERVAL_S)
        else:
            print("TIMEOUT: login not detected", flush=True)
            await context.close()
            return 1

        elapsed = time.monotonic() - start
        print(f"Login detected after {elapsed:.0f}s at {urlparse(page.url).path}", flush=True)
        await context.storage_state(path=str(STORAGE_STATE))
        cookies = await context.cookies()
        print(f"Saved storage_state ({len(cookies)} cookies) -> {STORAGE_STATE}", flush=True)
        print("Cookie names/domains (values not shown):", flush=True)
        for c in cookies:
            print(f"  {c['domain']:<28} {c['name']:<40} expires={c['expires']:.0f}", flush=True)
        await context.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
