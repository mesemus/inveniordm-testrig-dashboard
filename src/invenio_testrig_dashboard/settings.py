import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit

import yaml
from croniter import croniter
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, InitSettingsSource, SettingsConfigDict

REPOSITORY_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_SECRET_KEY_PARTS = ("token", "password", "secret")


def _origin(url: str) -> tuple[str, str, int]:
    """(scheme, host, port), treating a missing port as the scheme's default."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    return (
        scheme,
        (parsed.hostname or "").lower(),
        parsed.port or (443 if scheme == "https" else 80),
    )


def _find_secret_like_key(value: Any) -> str | None:
    """The first mapping key (at any depth) that looks like it holds a credential."""
    if isinstance(value, dict):
        for key, item in value.items():
            if any(part in str(key).lower() for part in _SECRET_KEY_PARTS):
                return str(key)
            found = _find_secret_like_key(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_secret_like_key(item)
            if found is not None:
                return found
    return None


class ConfigError(ValueError):
    """The configuration file is missing or cannot be used."""


def _yaml_source(settings_cls: type[BaseSettings], path: Path) -> InitSettingsSource:
    """Load config.yaml strictly: valid YAML, a mapping, and only known top-level keys.

    Parsed here (not via YamlConfigSettingsSource) so malformed files give a readable
    ConfigError instead of a traceback, without relying on pydantic-settings internals.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping of settings, not {type(data).__name__}")
    unknown = sorted(set(map(str, data)) - set(settings_cls.model_fields))
    if unknown:
        raise ConfigError(f"unknown key(s) in {path}: {', '.join(unknown)}")
    return InitSettingsSource(settings_cls, init_kwargs=data)


class Target(BaseModel):
    # A typo like "enabeld: false" must fail, not silently leave the target enabled.
    model_config = ConfigDict(extra="forbid")

    branch: str
    schedule: str
    enabled: bool = True

    @field_validator("branch")
    @classmethod
    def _branch_format(cls, value: str) -> str:
        # Branch names end up in API paths and frontend routes, where "/" breaks routing;
        # control characters (isprintable() is False for them) would also break Postgres text.
        if (
            not value
            or "/" in value
            or any(char.isspace() for char in value)
            or not value.isprintable()
        ):
            raise ValueError(
                "branch must be non-empty, printable, and contain no whitespace or '/'"
            )
        return value

    @field_validator("schedule")
    @classmethod
    def _schedule_valid(cls, value: str) -> str:
        # 5 fields (or a macro like @daily), as in k8s CronJobs; croniter would also accept a
        # seconds field, which would make the target due on every tick.
        is_macro = value.strip().startswith("@")
        if not croniter.is_valid(value) or (not is_macro and len(value.split()) != 5):
            raise ValueError("schedule must be a 5-field cron expression or a macro like @daily")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ITD_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    # Non-secret settings, normally read from config.yaml.
    seed_repository: str
    runner_repository: str
    # The testrig version is not a setting: it is a locked dependency, read from the installed
    # package metadata (dispatch.testrig_ref()). An old config key fails loudly here.
    # Optional full testrig config. None means testrig's built-in defaults are used
    # (with --repository overriding the seed); testrig does not merge partial configs.
    # Stored on every run and shown publicly: never put secrets here.
    testrig_config: dict[str, Any] | None = None
    targets: list[Target] = Field(default_factory=list)
    maintainers: list[str] = Field(default_factory=list)
    public_url: str = "http://localhost:8000"
    # Every page and API call needs a GitHub login. The web server refuses to start
    # without the OAuth settings; false is only for local development and the e2e test.
    require_login: bool = True
    # Where the built SPA lives; None means "frontend/dist" relative to the working directory.
    # A container sets this (or ITD_FRONTEND_DIST) instead of relying on its workdir.
    frontend_dist: Path | None = None
    s3_bucket: str
    s3_endpoint_url: str | None = None
    # What the *browser* can reach. Defaults to s3_endpoint_url; the host is part of the
    # presigned URL's signature, so it can't be rewritten after signing.
    s3_public_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    # Postgres backups: their own bucket, the same credentials. Only `backup` needs it.
    # Never the artifacts bucket: that one hands out presigned links to browsers.
    s3_backup_bucket: str | None = None
    # `backup` deletes dumps older than this after a successful upload.
    backup_retention_days: PositiveInt = 30
    lost_after_hours: NonNegativeInt = 6

    # Secrets, normally read from ITD_* environment variables.
    database_url: SecretStr
    github_token: SecretStr
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    discord_webhook_url: SecretStr | None = None
    discord_ops_webhook_url: SecretStr | None = None
    github_oauth_client_id: SecretStr | None = None
    github_oauth_client_secret: SecretStr | None = None
    session_secret: SecretStr | None = None

    @field_validator("testrig_config")
    @classmethod
    def _testrig_config_shape(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value.get("github"), list) or not isinstance(
            value.get("seed_repository"), dict
        ):
            raise ValueError(
                "testrig_config must be a complete testrig config with 'github' and 'seed_repository'"
            )
        # The config is stored on every run and shown publicly by the dashboard.
        secret_key = _find_secret_like_key(value)
        if secret_key is not None:
            raise ValueError(
                f"testrig_config must not contain secrets (key {secret_key!r}); it is public"
            )
        return value

    @field_validator("seed_repository", "runner_repository")
    @classmethod
    def _repository_format(cls, value: str) -> str:
        # "." and ".." would turn GitHub API paths like /repos/{repo} into path traversal.
        if not REPOSITORY_RE.fullmatch(value) or {".", ".."} & set(value.split("/")):
            raise ValueError("must look like 'org/repo'")
        return value

    @field_validator("public_url")
    @classmethod
    def _public_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("must be an http(s) URL")
        return value.rstrip("/")

    @field_validator("s3_public_endpoint_url")
    @classmethod
    def _s3_public_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("must be an http(s) URL")
        try:
            _origin(value)
        except ValueError as exc:
            raise ValueError("must be an http(s) URL with a valid port") from exc
        return value.rstrip("/")

    @model_validator(mode="after")
    def _s3_public_endpoint_is_a_separate_origin(self) -> Settings:
        # Uploaded artifacts include HTML/SVG, so presigned URLs must not share the
        # dashboard's origin (where they could reach the session cookie).
        # The effective presigning endpoint is s3_public_endpoint_url, else s3_endpoint_url.
        presign_url = self.s3_public_endpoint_url or self.s3_endpoint_url
        if presign_url is None:
            return self
        if _origin(presign_url) == _origin(self.public_url):
            setting = (
                "s3_public_endpoint_url"
                if self.s3_public_endpoint_url is not None
                else "s3_endpoint_url"
            )
            raise ValueError(f"{setting} must be a different origin than public_url")
        return self

    @model_validator(mode="after")
    def _backups_have_their_own_bucket(self) -> Settings:
        if self.s3_backup_bucket is not None and self.s3_backup_bucket == self.s3_bucket:
            raise ValueError(
                "s3_backup_bucket must differ from s3_bucket: the artifacts bucket presigns "
                "links for browsers, and a database dump must never be reachable that way"
            )
        return self

    @model_validator(mode="after")
    def _oauth_settings_are_complete(self) -> Settings:
        values = (self.github_oauth_client_id, self.github_oauth_client_secret, self.session_secret)
        provided = [value is not None for value in values]
        if any(provided) and not all(provided):
            raise ValueError(
                "github_oauth_client_id, github_oauth_client_secret and session_secret "
                "must be set together (or all left unset)"
            )
        if self.session_secret is not None and len(self.session_secret.get_secret_value()) < 32:
            raise ValueError("session_secret must be at least 32 characters")
        return self

    @model_validator(mode="after")
    def _unique_target_branches(self) -> Settings:
        seen: set[str] = set()
        for target in self.targets:
            if target.branch in seen:
                raise ValueError(f"duplicate target branch: {target.branch!r}")
            seen.add(target.branch)
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # Read ITD_CONFIG_FILE at instantiation time (not import time) so tests and callers
        # can point at different config files. Precedence: init > env > .env > YAML.
        sources = [init_settings, env_settings, dotenv_settings]
        configured = os.environ.get("ITD_CONFIG_FILE")
        yaml_file = Path(configured) if configured else Path("config.yaml")
        if configured and not yaml_file.is_file():
            raise ConfigError(f"config file not found: {yaml_file}")
        if yaml_file.is_file():
            sources.append(_yaml_source(settings_cls, yaml_file))
        return tuple(sources)
