import json
from pathlib import Path

import pytest

from zhaw_moodle_mcp.auth.session import SessionStore
from zhaw_moodle_mcp.config import BrowserConfig, Config, MoodleConfig, SessionConfig, SyncConfig

BASE = "https://moodle.example.ch"


def page(user_id: int, sesskey: str = "SK123", login_link: bool = False) -> str:
    link = f'<a href="{BASE}/login/index.php">Log in</a>' if login_link else ""
    return (
        "<html><head><script>M.cfg = {"
        f'"wwwroot":"{BASE}","sesskey":"{sesskey}","userId":{user_id}'
        f"}};</script></head><body>{link}</body></html>"
    )


LOGGED_IN_PAGE = page(4242)
GUEST_PAGE = page(1, login_link=True)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        moodle=MoodleConfig(base_url=BASE, download_directory=tmp_path / "ZHAW"),
        browser=BrowserConfig(profile_directory=tmp_path / "profile", auto_login=False),
        session=SessionConfig(storage_state=tmp_path / "auth" / "storage_state.json"),
        sync=SyncConfig(database=tmp_path / "moodle.db"),
    )


@pytest.fixture
def store(config: Config) -> SessionStore:
    """A store with a (fake) saved session."""
    state = config.session.storage_state
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"cookies": [
        {"name": "MoodleSession", "value": "abc", "domain": "moodle.example.ch", "path": "/"},
        {"name": "other", "value": "x", "domain": ".eduid.ch", "path": "/"},
    ]}))
    return SessionStore(state, config.browser.profile_directory)
