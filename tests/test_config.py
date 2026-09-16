from pathlib import Path

import pytest
from pydantic import ValidationError

from zhaw_moodle_mcp.config import BrowserConfig, load_config


def test_browser_defaults_to_auto():
    assert BrowserConfig().name == "auto"
    assert BrowserConfig().executable_path is None


def test_browser_name_from_toml(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[browser]\nname = "brave"\nexecutable_path = "~/thorium.exe"\n', encoding="utf-8")
    browser = load_config(path).browser
    assert browser.name == "brave"
    assert browser.executable_path == Path.home() / "thorium.exe"


def test_legacy_channel_key_still_works(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[browser]\nchannel = "msedge"\n', encoding="utf-8")
    assert load_config(path).browser.name == "msedge"


def test_unknown_browser_name_is_rejected():
    with pytest.raises(ValidationError):
        BrowserConfig(name="firefox")
