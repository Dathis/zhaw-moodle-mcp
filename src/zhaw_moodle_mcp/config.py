"""Configuration loaded from an optional TOML file with safe defaults.

Location: `<app dir>/config.toml`, overridable via the `ZHAW_MOODLE_MCP_CONFIG`
environment variable. A few settings can also come from environment variables
(used by the Claude Desktop extension). Passwords are never configured.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Literal

from platformdirs import user_config_dir, user_data_dir
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)

APP_NAME = "zhaw-moodle-mcp"
CONFIG_ENV_VAR = "ZHAW_MOODLE_MCP_CONFIG"
HOME_ENV_VAR = "ZHAW_MOODLE_MCP_HOME"
DOWNLOAD_DIR_ENV_VAR = "ZHAW_MOODLE_DOWNLOAD_DIR"
BROWSER_ENV_VAR = "ZHAW_MOODLE_BROWSER"

# Before v0.4 everything lived in the platform's app-data folders.
LEGACY_CONFIG_DIR = Path(user_config_dir(APP_NAME, appauthor=False))
LEGACY_DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False))


def _app_dirs() -> tuple[Path, Path]:
    """(config dir, data dir). On Windows both are ~/.zhaw-moodle-mcp: Claude from the
    Microsoft Store redirects AppData writes of the processes it starts into its own
    package folder, so a session stored there would be invisible to the CLI (and vice versa)."""
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        home = Path(os.path.expandvars(override)).expanduser()
        return home, home
    if sys.platform == "win32":
        home = Path.home() / f".{APP_NAME}"
        return home, home
    return LEGACY_CONFIG_DIR, LEGACY_DATA_DIR


CONFIG_DIR, DATA_DIR = _app_dirs()


def _expand(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoodleConfig(_Section):
    base_url: str = "https://moodle.zhaw.ch"
    download_directory: Path = Field(default_factory=lambda: Path.home() / "ZHAW")
    request_timeout: float = 60.0
    max_concurrent_requests: int = 4
    # Moodle shows dates as local text; used to interpret them
    timezone: str = "Europe/Zurich"

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("download_directory", mode="before")
    @classmethod
    def _expand_dir(cls, v: str | Path) -> Path:
        return _expand(v)


class BrowserConfig(_Section):
    # Browser for the login: "auto" = default browser if Chromium-based, else an installed one.
    # "chromium" is Playwright's bundled browser (needs `playwright install chromium`).
    # `channel` is the key used before v0.3.
    name: Literal["auto", "chrome", "msedge", "brave", "vivaldi", "chromium"] = Field(
        "auto", validation_alias=AliasChoices("name", "channel")
    )
    # Any other Chromium-based browser; overrides `name`
    executable_path: Path | None = None
    login_timeout: int = 300
    auto_login: bool = True
    profile_directory: Path = DATA_DIR / "browser-profile"

    @field_validator("profile_directory", "executable_path", mode="before")
    @classmethod
    def _expand_dir(cls, v: str | Path | None) -> Path | None:
        return None if v is None else _expand(v)


class SessionConfig(_Section):
    storage_state: Path = DATA_DIR / "auth" / "storage_state.json"
    # Skip re-validating the session against Moodle for this many seconds.
    validation_ttl: int = 120

    @field_validator("storage_state", mode="before")
    @classmethod
    def _expand_path(cls, v: str | Path) -> Path:
        return _expand(v)


class SyncConfig(_Section):
    database: Path = DATA_DIR / "moodle.db"

    @field_validator("database", mode="before")
    @classmethod
    def _expand_path(cls, v: str | Path) -> Path:
        return _expand(v)


class SearchConfig(_Section):
    # Re-read active courses before searching if the index is older than this
    index_max_age_minutes: int = 60


class Config(_Section):
    moodle: MoodleConfig = Field(default_factory=MoodleConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    return _expand(override) if override else CONFIG_DIR / "config.toml"


def migrate_legacy_files() -> None:
    """Copy config and metadata from the pre-v0.4 location once (the login is redone)."""
    if (CONFIG_DIR, DATA_DIR) == (LEGACY_CONFIG_DIR, LEGACY_DATA_DIR) or DATA_DIR.exists():
        return
    moves = [(LEGACY_CONFIG_DIR / "config.toml", CONFIG_DIR / "config.toml"),
             (LEGACY_DATA_DIR / "moodle.db", DATA_DIR / "moodle.db")]
    for old, new in moves:
        if old.is_file() and not new.exists():
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
                log.info("Copied %s from the previous app folder", old.name)
            except OSError as exc:
                log.warning("Could not copy %s: %s", old.name, exc.strerror)


def _apply_env(config: Config) -> Config:
    download_dir = os.environ.get(DOWNLOAD_DIR_ENV_VAR, "").strip()
    browser = os.environ.get(BROWSER_ENV_VAR, "").strip().lower()
    moodle = {"download_directory": download_dir} if download_dir else {}
    browser_settings = {"name": browser} if browser else {}
    if not moodle and not browser_settings:
        return config
    data = config.model_dump()
    data["moodle"].update(moodle)
    data["browser"].update(browser_settings)
    return Config.model_validate(data)


def load_config(path: Path | None = None) -> Config:
    """Settings from the TOML file, then environment variables."""
    if path is None:
        migrate_legacy_files()
        path = config_path()
    config = Config()
    if path.exists():
        with path.open("rb") as f:
            config = Config.model_validate(tomllib.load(f))
    return _apply_env(config)
