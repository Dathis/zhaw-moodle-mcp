"""Shared paths for the spike scripts. Auth data lives outside the repository."""

from pathlib import Path

from platformdirs import user_data_dir

BASE_URL = "https://moodle.zhaw.ch"

DATA_DIR = Path(user_data_dir("zhaw-moodle-mcp", appauthor=False))
PROFILE_DIR = DATA_DIR / "browser-profile"
STORAGE_STATE = DATA_DIR / "auth" / "storage_state.json"

OUTPUT_DIR = Path(__file__).parent / "output"
