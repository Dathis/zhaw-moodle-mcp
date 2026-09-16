from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AuthStatus(BaseModel):
    authenticated: bool
    state: Literal["valid", "expired", "missing"]
    moodle_url: str
    message: str


class LogoutResult(BaseModel):
    logged_out: bool
    server_session_invalidated: bool
    message: str
