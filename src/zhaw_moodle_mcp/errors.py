"""Domain errors with stable codes that are safe to show to the AI client."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    LOGIN_FAILED = "LOGIN_FAILED"
    COURSE_NOT_FOUND = "COURSE_NOT_FOUND"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_NOT_DOWNLOADABLE = "RESOURCE_NOT_DOWNLOADABLE"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    MOODLE_UNAVAILABLE = "MOODLE_UNAVAILABLE"
    MOODLE_ERROR = "MOODLE_ERROR"


class MoodleError(Exception):
    """Messages must never contain cookies, sesskeys or raw HTML."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class AuthRequired(MoodleError):
    def __init__(self, message: str = "No valid Moodle session. Call moodle_login.") -> None:
        super().__init__(ErrorCode.AUTH_REQUIRED, message)


class SessionExpired(MoodleError):
    def __init__(self, message: str = "The Moodle session has expired.") -> None:
        super().__init__(ErrorCode.SESSION_EXPIRED, message)
