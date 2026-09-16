import json
import re
from pathlib import Path

from zhaw_moodle_mcp import config as config_module

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_basics():
    assert MANIFEST["server"]["type"] == "uv"
    assert (ROOT / MANIFEST["server"]["entry_point"]).is_file()
    assert (ROOT / "extension" / MANIFEST["icon"]).is_file()
    # filled in by scripts/build_extension.py from pyproject.toml and the running server
    assert MANIFEST["version"] == "0.0.0" and MANIFEST["tools"] == []


def test_manifest_env_matches_config():
    env = MANIFEST["server"]["mcp_config"]["env"]
    assert set(env) == {config_module.DOWNLOAD_DIR_ENV_VAR, config_module.BROWSER_ENV_VAR}
    referenced = {m for value in env.values() for m in re.findall(r"\$\{user_config\.(\w+)\}", value)}
    assert referenced == set(MANIFEST["user_config"])


def test_manifest_runs_the_package_module():
    args = MANIFEST["server"]["mcp_config"]["args"]
    assert args[-2:] == ["-m", "zhaw_moodle_mcp"]
    assert "--frozen" in args and "${__dirname}" in args
