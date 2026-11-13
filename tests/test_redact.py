import io
import logging

from pydantic import SecretStr
from sqlalchemy.engine import make_url

from invenio_testrig_dashboard.redact import RedactingFilter, install_logging_filter, redact
from invenio_testrig_dashboard.settings import Settings


def all_secrets_settings() -> Settings:
    return Settings(
        seed_repository="oarepo/seed",
        runner_repository="oarepo/runner",
        s3_bucket="bucket",
        database_url="postgresql://user:secretdb@host/db",
        github_token="ghp_secret",
        s3_access_key="AKIASECRET",
        s3_secret_key="s3secret",
        discord_webhook_url="https://discord.com/api/webhooks/123/SECRETHOOK",
        discord_ops_webhook_url="https://discord.com/api/webhooks/456/OPSHOOK",
        github_oauth_client_id="oauthid",
        github_oauth_client_secret="oauthsecret",
        session_secret="a-very-long-test-session-secret-0123456789",
    )


def test_redact_replaces_every_secret_in_longer_text():
    settings = all_secrets_settings()
    text = (
        "db=postgresql://user:secretdb@host/db token=ghp_secret key=AKIASECRET "
        "s3=s3secret hook=https://discord.com/api/webhooks/123/SECRETHOOK "
        "ops=https://discord.com/api/webhooks/456/OPSHOOK "
        "oauth=oauthsecret session=a-very-long-test-session-secret-0123456789"
    )

    result = redact(text, settings)

    for secret in (
        "secretdb",
        "ghp_secret",
        "AKIASECRET",
        "s3secret",
        "SECRETHOOK",
        "OPSHOOK",
        "oauthsecret",
        "a-very-long-test-session-secret-0123456789",
    ):
        assert secret not in result
    assert "***" in result


def test_redact_leaves_text_without_secrets_alone(settings):
    assert redact("nothing to redact", settings) == "nothing to redact"


def test_redact_composite_secret_parts():
    settings = all_secrets_settings().model_copy(
        update={"database_url": SecretStr("postgresql://user:p%40ss@host/db")}
    )
    text = "password p@ss / encoded p%40ss / token SECRETHOOK / ops OPSHOOK"

    result = redact(text, settings)

    for part in ("p@ss", "p%40ss", "SECRETHOOK", "OPSHOOK"):
        assert part not in result
    assert result.count("***") == 4


def test_redact_whole_url_before_its_parts():
    settings = all_secrets_settings()

    assert redact("url postgresql://user:secretdb@host/db", settings) == "url ***"


def test_logging_filter_redacts_messages_and_exception_text(settings):
    settings = settings.model_copy(
        update={"database_url": SecretStr("postgresql://user:topsecretpw@host/db")}
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter(settings))
    logger = logging.getLogger("itd.test.redact")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        try:
            raise RuntimeError("connection failed with topsecretpw")
        except RuntimeError:
            logger.exception("login with topsecretpw failed")
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert "topsecretpw" not in output
    assert "***" in output


def test_logging_filter_redacts_formatted_args(settings):
    settings = settings.model_copy(
        update={"database_url": SecretStr("postgresql://user:argsecretpw@host/db")}
    )

    class ArgsFormatter(logging.Formatter):
        def formatMessage(self, record: logging.LogRecord) -> str:
            return f"{record.msg} | {record.args}"

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ArgsFormatter())
    handler.addFilter(RedactingFilter(settings))
    logger = logging.getLogger("itd.test.redact.args")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("db %s", "postgresql://user:argsecretpw@host/db")
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert "argsecretpw" not in output
    assert "***" in output


def test_logging_filter_redacts_httpx2_url_arguments(settings, caplog):
    import httpx2

    from invenio_testrig_dashboard import discord

    url = "https://discord.com/api/webhooks/123/TOPSECRETWEBHOOKTOKEN"
    settings = settings.model_copy(update={"discord_webhook_url": SecretStr(url)})
    install_logging_filter(settings)

    with caplog.at_level(logging.INFO):
        discord.post(
            url, "hi", transport=httpx2.MockTransport(lambda request: httpx2.Response(204))
        )

    # httpx2 logs the request URL as an object argument, not a string.
    assert "TOPSECRETWEBHOOKTOKEN" not in caplog.text
    assert "***" in caplog.text


def test_logging_filter_redacts_object_arguments(settings):
    settings = settings.model_copy(
        update={"database_url": SecretStr("postgresql://user:objsecretpw@host/db")}
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter(settings))
    logger = logging.getLogger("itd.test.redact.object")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        try:
            raise RuntimeError("failed with objsecretpw")
        except RuntimeError as exc:
            logger.error("boom %s", exc)
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert "objsecretpw" not in output
    assert "***" in output


def test_logging_filter_keeps_uvicorn_access_args(settings):
    from uvicorn.logging import AccessFormatter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", "/api/health", "1.1", 200),
        None,
    )

    RedactingFilter(settings).filter(record)

    assert len(record.args) == 5
    # Would raise TypeError with args cleared (uvicorn reads record.args).
    assert "200" in AccessFormatter().format(record)


def test_logging_filter_redacts_a_root_handler(settings):
    settings = settings.model_copy(
        update={"database_url": SecretStr("postgresql://user:rootsecretpw@host/db")}
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        install_logging_filter(settings)
        root.error("login with rootsecretpw failed")
    finally:
        root.removeHandler(handler)

    assert "rootsecretpw" not in stream.getvalue()
    assert "***" in stream.getvalue()


def test_install_logging_filter_covers_non_propagating_loggers(settings):
    """uvicorn logs through its own handlers with propagate=False."""
    import io

    password = make_url(settings.database_url.get_secret_value()).password
    stream = io.StringIO()
    logger = logging.getLogger("itd-test.uvicorn-like")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        install_logging_filter(settings)
        logger.error("db connect failed for password %s", password)
    finally:
        logger.removeHandler(handler)

    assert password not in stream.getvalue()
    assert "***" in stream.getvalue()
