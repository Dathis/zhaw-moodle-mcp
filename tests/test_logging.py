import logging

from zhaw_moodle_mcp.logging_setup import RedactingFormatter, redact


def test_redacts_secrets():
    text = redact(
        "POST /lib/ajax/service.php?sesskey=AbC123&info=x "
        'Cookie: MoodleSession=s3cr3t; other=1 {"sesskey":"XyZ"} '
        "_shibsession_6465=val SAMLResponse=PHNhbWw&RelayState=abc"
    )
    for secret in ("AbC123", "s3cr3t", "XyZ", "=val", "PHNhbWw", "=abc"):
        assert secret not in text


def test_formatter_redacts_args():
    record = logging.LogRecord("t", logging.INFO, "", 0, "url %s", ("x?sesskey=abc",), None)
    assert "abc" not in RedactingFormatter("%(message)s").format(record)
