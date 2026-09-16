import os
from pathlib import Path

import pytest
import respx

from zhaw_moodle_mcp import config as config_module
from zhaw_moodle_mcp.config import Config, load_config
from zhaw_moodle_mcp.errors import AuthRequired
from zhaw_moodle_mcp.service import MoodleService

from .fake_moodle import FakeMoodle


def test_windows_uses_home_folder(monkeypatch):
    monkeypatch.delenv(config_module.HOME_ENV_VAR, raising=False)
    monkeypatch.setattr(config_module.sys, "platform", "win32")
    assert config_module._app_dirs() == (Path.home() / ".zhaw-moodle-mcp",) * 2


def test_other_platforms_keep_platform_dirs(monkeypatch):
    monkeypatch.delenv(config_module.HOME_ENV_VAR, raising=False)
    monkeypatch.setattr(config_module.sys, "platform", "darwin")
    assert config_module._app_dirs() == (config_module.LEGACY_CONFIG_DIR, config_module.LEGACY_DATA_DIR)


def test_home_override(monkeypatch, tmp_path):
    monkeypatch.setenv(config_module.HOME_ENV_VAR, str(tmp_path / "x"))
    assert config_module._app_dirs() == (tmp_path / "x", tmp_path / "x")


def test_env_overrides_file_settings(monkeypatch, tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text('[moodle]\ndownload_directory = "~/Elsewhere"\n[browser]\nname = "chrome"\n', encoding="utf-8")
    monkeypatch.setenv(config_module.DOWNLOAD_DIR_ENV_VAR, str(tmp_path / "Studium"))
    monkeypatch.setenv(config_module.BROWSER_ENV_VAR, "MSEdge")
    config = load_config(toml)
    assert config.moodle.download_directory == tmp_path / "Studium"
    assert config.browser.name == "msedge"


def test_empty_env_values_are_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv(config_module.DOWNLOAD_DIR_ENV_VAR, "  ")
    monkeypatch.setenv(config_module.BROWSER_ENV_VAR, "")
    assert load_config(tmp_path / "missing.toml") == Config()


def test_invalid_browser_env_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv(config_module.BROWSER_ENV_VAR, "firefox")
    with pytest.raises(ValueError):
        load_config(tmp_path / "missing.toml")


def test_migrates_legacy_files_once(monkeypatch, tmp_path):
    legacy_cfg, legacy_data, new = tmp_path / "cfg", tmp_path / "data", tmp_path / "new"
    legacy_cfg.mkdir()
    legacy_data.mkdir()
    (legacy_cfg / "config.toml").write_text("[moodle]\n", encoding="utf-8")
    (legacy_data / "moodle.db").write_bytes(b"db")
    (legacy_data / "auth").mkdir()
    monkeypatch.setattr(config_module, "LEGACY_CONFIG_DIR", legacy_cfg)
    monkeypatch.setattr(config_module, "LEGACY_DATA_DIR", legacy_data)
    monkeypatch.setattr(config_module, "CONFIG_DIR", new)
    monkeypatch.setattr(config_module, "DATA_DIR", new)

    config_module.migrate_legacy_files()
    assert sorted(os.listdir(new)) == ["config.toml", "moodle.db"]  # the login is not copied

    (legacy_data / "moodle.db").write_bytes(b"newer")
    config_module.migrate_legacy_files()
    assert (new / "moodle.db").read_bytes() == b"db"


def test_no_migration_when_dirs_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_module.LEGACY_CONFIG_DIR)
    monkeypatch.setattr(config_module, "DATA_DIR", config_module.LEGACY_DATA_DIR)
    monkeypatch.setattr(config_module.shutil, "copy2", lambda *a: pytest.fail("must not copy"))
    config_module.migrate_legacy_files()


@pytest.fixture
def moodle():
    fake = FakeMoodle()
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
        yield fake


async def test_running_server_follows_logout_from_cli(config, store, moodle):
    server = MoodleService(config)
    try:
        await server.list_courses(None)  # session validated and cached

        cli = MoodleService(config)
        await cli.logout()
        await cli.aclose()

        # without the check the cached validation would keep the old cookies alive
        with pytest.raises(AuthRequired):  # auto_login is off in tests
            await server.list_courses(None)
    finally:
        await server.aclose()


async def test_running_server_picks_up_new_login(config, store, moodle):
    server = MoodleService(config)
    try:
        await server.list_courses(None)
        before = server.client.http
        state = config.session.storage_state
        state.write_text(state.read_text(encoding="utf-8") + " ", encoding="utf-8")  # a fresh login elsewhere
        await server.list_courses(None)
        assert server.client.http is not before
    finally:
        await server.aclose()
