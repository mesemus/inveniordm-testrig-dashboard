"""Replace secret settings values in external text before storing, logging, or posting it."""

import logging
from collections.abc import Iterator
from urllib.parse import quote, urlsplit

from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .settings import Settings

REPLACEMENT = "***"


def _secret_parts(name: str, secret: str) -> Iterator[str]:
    """The secret itself, plus the parts of composite secrets that can appear on their own."""
    yield secret
    if name == "database_url":
        # A driver or pg_dump error may show the password without the rest of the URL.
        try:
            password = make_url(secret).password
        except ArgumentError:
            password = None
        if password:
            yield str(password)
            yield quote(str(password), safe="")
    elif name.startswith("discord_") and name.endswith("_webhook_url"):
        # https://discord.com/api/webhooks/<id>/<token>: the token alone is enough to post.
        token = urlsplit(secret).path.rstrip("/").rpartition("/")[2]
        if token:
            yield token


def redact(text: str, settings: Settings) -> str:
    parts: set[str] = set()
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr) and value.get_secret_value():
            parts.update(part for part in _secret_parts(name, value.get_secret_value()) if part)
    # Longest first, so a whole URL is replaced before its own password inside it.
    for part in sorted(parts, key=len, reverse=True):
        text = text.replace(part, REPLACEMENT)
    return text


class RedactingFilter(logging.Filter):
    """Redact every log record, message and exception text alike, in one place.

    Per-call redaction doesn't scale: call sites log raw exception texts (``log.exception``)
    that may contain secrets.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg, self.settings)
        # Redact the string parts of the arguments instead of clearing them: some formatters
        # (uvicorn's AccessFormatter) read record.args directly.
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._redact(value) for key, value in record.args.items()}
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self.settings)
        return True

    def _redact(self, value: object) -> object:
        # Numbers stay numbers (`%d` in uvicorn's access log); other objects are checked
        # through both str() and repr() (a library may log an object such as httpx2.URL).
        if value is None or isinstance(value, (bool, int, float)):
            return value
        text = str(value)
        redacted_text = redact(text, self.settings)
        redacted_repr = redact(repr(value), self.settings)
        if redacted_text != text or redacted_repr != repr(value):
            return redacted_text
        return value


def install_logging_filter(settings: Settings) -> None:
    """Install (or replace) the redacting filter on every handler that exists right now.

    Covers the root handlers *and* handlers attached to other loggers: some libraries
    (uvicorn) give their loggers their own handlers and turn propagation off, so a
    filter on the root handlers alone would never see their records. Call this again after
    anything configures logging.
    """
    redacting = RedactingFilter(settings)
    handlers = list(logging.getLogger().handlers)
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):  # skip PlaceHolder entries
            handlers.extend(logger.handlers)
    for handler in dict.fromkeys(handlers):  # dedupe, keep order
        for existing in list(handler.filters):
            if isinstance(existing, RedactingFilter):
                handler.removeFilter(existing)
        handler.addFilter(redacting)
