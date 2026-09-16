"""Logging to stderr (stdout carries the MCP stdio protocol) with secret redaction."""

from __future__ import annotations

import logging
import re
import sys

_REDACTIONS = [
    # sesskey in URLs, JSON and form data
    (re.compile(r"(sesskey[\"']?\s*[=:]\s*[\"']?)[\w-]+", re.I), r"\1[REDACTED]"),
    # Cookie / Authorization headers
    (re.compile(r"((?:set-)?cookie|authorization)(\s*[:=]\s*)[^\r\n]+", re.I), r"\1\2[REDACTED]"),
    # Moodle and Shibboleth session cookies
    (re.compile(r"(MoodleSession\w*|_shibsession_\w+|shib_idp_session|JSESSIONID)=[^;\s,]+", re.I),
     r"\1=[REDACTED]"),
    # SAML / SSO parameters
    (re.compile(r"((?:SAMLRequest|SAMLResponse|RelayState|execution)=)[^&\s]+", re.I), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs (including sesskey) at INFO.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
