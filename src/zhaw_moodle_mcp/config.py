"""Configuration loaded from an optional TOML file with safe defaults.

Location: `<user config dir>/zhaw-moodle-mcp/config.toml`, overridable via the
`ZHAW_MOODLE_MCP_CONFIG` environment variable. Passwords are never configured.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, ConfigDict, Field, field_validator

APP_NAME = "zhaw-moodle-mcp"
CONFIG_ENV_VAR = "ZHAW_MOODLE_MCP_CONFIG"

CONFIG_DIR = Path(user_config_dir(APP_NAME, appauthor=False))
DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False))


def _expand(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoodleConfig(_Section):
    base_url: str = "https://moodle.zhaw.ch"
    download_directory: Path = Field(default_factory=lambda: Path.home() / "ZHAW")
    request_timeout: float = 60.0
    max_concurrent_requests: int = 4

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("download_directory", mode="before")
    @classmethod
    def _expand_dir(cls, v: str | Path) -> Path:
        return _expand(v)


class BrowserConfig(_Section):
    # "chrome"/"msedge" use the installed browser; "chromium" needs `playwright install chromium`.
    channel: Literal["chrome", "msedge", "chromium"] = "chrome"
    login_timeout: int = 300
    auto_login: bool = True
    profile_directory: Path = DATA_DIR / "browser-profile"

    @field_validator("profile_directory", mode="before")
    @classmethod
    def _expand_dir(cls, v: str | Path) -> Path:
        return _expand(v)


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


class Config(_Section):
    moodle: MoodleConfig = Field(default_factory=MoodleConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    return _expand(override) if override else CONFIG_DIR / "config.toml"


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        return Config.model_validate(tomllib.load(f))
