import pytest
import yaml
from pydantic import ValidationError

from invenio_testrig_dashboard.settings import ConfigError, Settings

BASE_CONFIG = {
    "seed_repository": "oarepo/invenio-rdm-seed",
    "runner_repository": "oarepo/invenio-testrig-dashboard-runner",
    "s3_bucket": "invenio-testrig-dashboard",
    "targets": [{"branch": "master", "schedule": "0 1 * * *"}],
    "database_url": "postgresql://dbuser:dbpass@localhost:25432/db",
    "github_token": "ghp_verysecret",
    "s3_access_key": "AKIAVERYSECRET",
    "s3_secret_key": "supersecretkey",
}


def load(tmp_path, monkeypatch, config=None) -> Settings:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(BASE_CONFIG if config is None else config))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ITD_CONFIG_FILE", str(path))
    return Settings()


def test_loads_yaml_file(tmp_path, monkeypatch):
    settings = load(tmp_path, monkeypatch)

    assert settings.seed_repository == "oarepo/invenio-rdm-seed"
    assert settings.s3_bucket == "invenio-testrig-dashboard"
    assert settings.s3_region == "us-east-1"
    assert settings.lost_after_hours == 6
    assert settings.targets[0].branch == "master"
    assert settings.targets[0].enabled is True


def test_env_var_overrides_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("ITD_S3_REGION", "eu-central-1")

    assert load(tmp_path, monkeypatch).s3_region == "eu-central-1"


def test_nested_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ITD_TARGETS",
        '[{"branch": "14.x", "schedule": "0 2 * * *", "enabled": false}]',
    )

    settings = load(tmp_path, monkeypatch)

    assert [(t.branch, t.enabled) for t in settings.targets] == [("14.x", False)]


def test_missing_required_field_is_named(tmp_path, monkeypatch):
    config = {k: v for k, v in BASE_CONFIG.items() if k != "seed_repository"}

    with pytest.raises(ValidationError, match="seed_repository"):
        load(tmp_path, monkeypatch, config)


def test_duplicate_target_branch_is_named(tmp_path, monkeypatch):
    config = BASE_CONFIG | {
        "targets": [
            {"branch": "master", "schedule": "0 1 * * *"},
            {"branch": "master", "schedule": "0 2 * * *"},
        ]
    }

    with pytest.raises(ValidationError, match="master"):
        load(tmp_path, monkeypatch, config)


def test_validation_error_hides_secret_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("ITD_S3_SECRET_KEY", "s3TOPSECRET")
    config = BASE_CONFIG | {
        "targets": [
            {"branch": "master", "schedule": "0 1 * * *"},
            {"branch": "master", "schedule": "0 2 * * *"},
        ]
    }

    with pytest.raises(ValidationError) as excinfo:
        load(tmp_path, monkeypatch, config)

    text = str(excinfo.value)
    assert "duplicate target branch" in text
    assert "s3TOPSECRET" not in text


@pytest.mark.parametrize("field", ["seed_repository", "runner_repository"])
@pytest.mark.parametrize("value", ["not-a-repo", "../..", "org/.."])
def test_invalid_repository_format(tmp_path, monkeypatch, field, value):
    config = BASE_CONFIG | {field: value}

    with pytest.raises(ValidationError, match=field):
        load(tmp_path, monkeypatch, config)


@pytest.mark.parametrize("branch", ["", "release/14", "has space"])
def test_invalid_target_branch(tmp_path, monkeypatch, branch):
    config = BASE_CONFIG | {"targets": [{"branch": branch, "schedule": "0 1 * * *"}]}

    with pytest.raises(ValidationError, match="branch"):
        load(tmp_path, monkeypatch, config)


def test_public_url_trailing_slash_is_stripped(tmp_path, monkeypatch):
    settings = load(tmp_path, monkeypatch, BASE_CONFIG | {"public_url": "http://localhost:8000/"})

    assert settings.public_url == "http://localhost:8000"


def test_invalid_public_url(tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="public_url"):
        load(tmp_path, monkeypatch, BASE_CONFIG | {"public_url": "ftp://example.com"})


def test_negative_lost_after_hours_is_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="lost_after_hours"):
        load(tmp_path, monkeypatch, BASE_CONFIG | {"lost_after_hours": -1})


def test_unknown_yaml_key_is_named(tmp_path, monkeypatch):
    config = BASE_CONFIG | {"taregts": []}

    with pytest.raises(ConfigError, match="taregts"):
        load(tmp_path, monkeypatch, config)


def test_invalid_cron_schedule_is_rejected(tmp_path, monkeypatch):
    config = BASE_CONFIG | {"targets": [{"branch": "master", "schedule": "not a cron"}]}

    with pytest.raises(ValidationError, match="schedule"):
        load(tmp_path, monkeypatch, config)


def test_testrig_config_is_loaded(tmp_path, monkeypatch):
    config = BASE_CONFIG | {
        "testrig_config": {"github": [], "seed_repository": {"git": "x/y@main"}}
    }

    settings = load(tmp_path, monkeypatch, config)

    assert settings.testrig_config == {"github": [], "seed_repository": {"git": "x/y@main"}}


def test_partial_testrig_config_is_rejected(tmp_path, monkeypatch):
    config = BASE_CONFIG | {"testrig_config": {"seed_repository": {}}}

    with pytest.raises(ValidationError, match="complete testrig config"):
        load(tmp_path, monkeypatch, config)


def test_env_file_ignores_unrelated_variables(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(BASE_CONFIG))
    (tmp_path / ".env").write_text("FOO=1\nITD_UNKNOWN=2\nITD_CONFIG_FILE=/does/not/exist\n")
    monkeypatch.setenv("ITD_CONFIG_FILE", str(config))

    settings = Settings()

    assert settings.s3_bucket == "invenio-testrig-dashboard"


def test_missing_explicit_config_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ITD_CONFIG_FILE", str(tmp_path / "nope.yaml"))

    with pytest.raises(ConfigError, match="config file not found"):
        Settings()


def test_secrets_are_masked(tmp_path, monkeypatch):
    settings = load(tmp_path, monkeypatch)
    text = f"{settings} {settings!r}"

    for secret in ("dbpass", "ghp_verysecret", "AKIAVERYSECRET", "supersecretkey"):
        assert secret not in text
    assert "**********" in text


def test_unknown_target_key_is_rejected(tmp_path, monkeypatch):
    config = BASE_CONFIG | {
        "targets": [{"branch": "master", "schedule": "0 1 * * *", "enabeld": False}]
    }

    with pytest.raises(ValidationError, match="enabeld"):
        load(tmp_path, monkeypatch, config)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("targets: [ {branch: master", "invalid YAML"),
        ("- just\n- a list\n", "must contain a mapping"),
        ("hello\n", "must contain a mapping"),
    ],
)
def test_malformed_yaml_is_a_config_error(tmp_path, monkeypatch, content, message):
    path = tmp_path / "config.yaml"
    path.write_text(content)
    monkeypatch.setenv("ITD_CONFIG_FILE", str(path))

    with pytest.raises(ConfigError, match=message):
        Settings()


def test_empty_yaml_file_is_allowed(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("")
    monkeypatch.setenv("ITD_CONFIG_FILE", str(path))

    with pytest.raises(ValidationError, match="seed_repository"):
        Settings()


@pytest.mark.parametrize("schedule", ["0 1 * * * *", "* * * *"])
def test_cron_schedule_must_have_five_fields(tmp_path, monkeypatch, schedule):
    config = BASE_CONFIG | {"targets": [{"branch": "master", "schedule": schedule}]}

    with pytest.raises(ValidationError, match="5-field"):
        load(tmp_path, monkeypatch, config)


def test_cron_macro_is_accepted(tmp_path, monkeypatch):
    config = BASE_CONFIG | {"targets": [{"branch": "master", "schedule": "@daily"}]}

    assert load(tmp_path, monkeypatch, config).targets[0].schedule == "@daily"


def test_testrig_config_must_not_contain_secrets(tmp_path, monkeypatch):
    config = BASE_CONFIG | {
        "testrig_config": {
            "github": [{"org": "x", "auth": {"Api_Token": "abc"}}],
            "seed_repository": {"git": "org/repo@master"},
        }
    }

    with pytest.raises(ValidationError, match="Api_Token"):
        load(tmp_path, monkeypatch, config)


def test_s3_public_endpoint_is_unset_by_default(tmp_path, monkeypatch):
    assert load(tmp_path, monkeypatch).s3_public_endpoint_url is None


def test_s3_public_endpoint_must_be_a_different_origin(tmp_path, monkeypatch):
    config = BASE_CONFIG | {
        "public_url": "https://dash.example",
        "s3_public_endpoint_url": "https://dash.example:443",
    }

    with pytest.raises(ValidationError, match="different origin"):
        load(tmp_path, monkeypatch, config)


def test_s3_public_endpoint_accepts_another_origin(tmp_path, monkeypatch):
    config = BASE_CONFIG | {
        "public_url": "https://dash.example",
        "s3_public_endpoint_url": "https://s3.example:9000",
    }

    assert load(tmp_path, monkeypatch, config).s3_public_endpoint_url == "https://s3.example:9000"


@pytest.mark.parametrize("value", ["ftp://s3.example", "not-a-url", "https://"])
def test_s3_public_endpoint_must_be_http(tmp_path, monkeypatch, value):
    with pytest.raises(ValidationError, match="http"):
        load(tmp_path, monkeypatch, BASE_CONFIG | {"s3_public_endpoint_url": value})


def test_presigning_endpoint_falls_back_to_s3_endpoint_url_for_the_check(tmp_path, monkeypatch):
    config = BASE_CONFIG | {
        "public_url": "https://dash.example",
        "s3_endpoint_url": "https://dash.example/s3",
    }

    with pytest.raises(ValidationError, match="s3_endpoint_url must be a different origin"):
        load(tmp_path, monkeypatch, config)


@pytest.mark.parametrize("branch", ["a\x00b", "a\x01b"])
def test_branch_rejects_control_characters(tmp_path, monkeypatch, branch):
    config = BASE_CONFIG | {
        "targets": [{"branch": branch, "schedule": "0 1 * * *"}],
    }

    with pytest.raises(ValidationError, match="branch"):
        load(tmp_path, monkeypatch, config)


def test_oauth_settings_must_be_set_together(tmp_path, monkeypatch):
    monkeypatch.setenv("ITD_GITHUB_OAUTH_CLIENT_ID", "id")

    with pytest.raises(ValidationError, match="set together"):
        load(tmp_path, monkeypatch)


def test_session_secret_must_be_at_least_32_characters(tmp_path, monkeypatch):
    monkeypatch.setenv("ITD_GITHUB_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("ITD_GITHUB_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ITD_SESSION_SECRET", "short")

    with pytest.raises(ValidationError, match="32 characters"):
        load(tmp_path, monkeypatch)


def test_complete_oauth_settings_load(tmp_path, monkeypatch):
    monkeypatch.setenv("ITD_GITHUB_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("ITD_GITHUB_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ITD_SESSION_SECRET", "x" * 32)

    settings = load(tmp_path, monkeypatch)

    assert settings.github_oauth_client_id is not None
    assert settings.session_secret is not None
