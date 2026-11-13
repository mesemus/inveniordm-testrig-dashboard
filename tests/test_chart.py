"""The Helm chart, rendered with `helm template`.

kubeconform validates the schema in CI; these tests check what a schema can't: the restricted
Pod Security settings CERIT enforces (devops.md §9), the secret keys each workload needs,
and the values the chart must refuse. values.yaml is the one (CERIT) deployment, so a plain
render is exactly what gets installed.
"""

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).parent.parent / "charts" / "invenio-testrig-dashboard"
VALUES = CHART / "values.yaml"
SECRET = "invenio-testrig-dashboard"

HELM = shutil.which("helm")
pytestmark = pytest.mark.skipif(HELM is None, reason="helm is not installed")

OAUTH_KEYS = {"ITD_GITHUB_OAUTH_CLIENT_ID", "ITD_GITHUB_OAUTH_CLIENT_SECRET", "ITD_SESSION_SECRET"}
DISCORD_KEYS = {"ITD_DISCORD_WEBHOOK_URL", "ITD_DISCORD_OPS_WEBHOOK_URL"}
BASE_KEYS = {"ITD_DATABASE_URL", "ITD_GITHUB_TOKEN", "ITD_S3_ACCESS_KEY", "ITD_S3_SECRET_KEY"}


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    assert HELM is not None
    return subprocess.run(  # noqa: S603 - the chart's own files and fixed --set values
        [HELM, "template", "itd", str(CHART), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def render(*args: str) -> list[dict]:
    result = _helm(*args)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def render_error(*args: str) -> str:
    result = _helm(*args)
    assert result.returncode != 0, "the chart rendered, but it should have refused"
    return result.stderr


def by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [doc for doc in docs if doc["kind"] == kind]


def pod_specs(docs: list[dict]) -> dict[str, dict]:
    specs = {}
    for doc in docs:
        if doc["kind"] in ("Deployment", "StatefulSet"):
            specs[doc["metadata"]["name"]] = doc["spec"]["template"]["spec"]
        elif doc["kind"] == "CronJob":
            specs[doc["metadata"]["name"]] = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return specs


def containers(spec: dict) -> list[dict]:
    return spec.get("initContainers", []) + spec["containers"]


def required_keys(container: dict) -> set[str]:
    return {
        env["name"]
        for env in container.get("env", [])
        if "secretKeyRef" in env.get("valueFrom", {})
    }


@pytest.fixture(scope="module")
def deployed_docs():
    """What `helm upgrade --install` puts on CERIT."""
    return render()


@pytest.fixture(scope="module")
def default_docs():
    """The deployment, with every optional workload on (today: the same as deployed)."""
    return render("--set", "backup.enabled=true", "--set", "dispatch.enabled=true")


def test_every_pod_passes_the_restricted_pod_security_standard(default_docs, deployed_docs):
    # web, postgres, dispatch, ingest, backup.
    for docs, count in ((default_docs, 5), (deployed_docs, 5)):
        specs = pod_specs(docs)
        assert len(specs) == count, specs.keys()
        for name, spec in specs.items():
            pod = spec["securityContext"]
            assert pod["runAsNonRoot"] is True, name
            assert isinstance(pod["runAsUser"], int) and pod["runAsUser"] > 0, name
            assert pod["seccompProfile"] == {"type": "RuntimeDefault"}, name
            assert spec["automountServiceAccountToken"] is False, name
            for container in containers(spec):
                sc = container["securityContext"]
                where = f"{name}/{container['name']}"
                assert sc["allowPrivilegeEscalation"] is False, where
                assert sc["capabilities"]["drop"] == ["ALL"], where
                assert sc["readOnlyRootFilesystem"] is True, where
                assert "privileged" not in sc, where


def test_every_container_sets_requests_and_limits(default_docs):
    # Without them CERIT's LimitRange gives each container 1 CPU, and memory/ephemeral limits
    # must trigger the app's own limits before an eviction.
    for name, spec in pod_specs(default_docs).items():
        for container in containers(spec):
            resources = container["resources"]
            for kind in ("requests", "limits"):
                assert {"cpu", "memory", "ephemeral-storage"} <= resources[kind].keys(), (
                    f"{name}/{container['name']} {kind}"
                )


def test_app_containers_have_writable_tmp_and_home(default_docs):
    for name, spec in pod_specs(default_docs).items():
        if name.endswith("-postgres"):
            continue
        volumes = {volume["name"]: volume for volume in spec["volumes"]}
        for container in containers(spec):
            mounts = {mount["mountPath"]: mount["name"] for mount in container["volumeMounts"]}
            assert "emptyDir" in volumes[mounts["/tmp"]], name  # noqa: S108 - a mount path
            assert "emptyDir" in volumes[mounts["/home/app"]], name
            assert mounts["/etc/invenio-testrig-dashboard/config.yaml"] == "config"


def test_required_secret_keys_per_workload(default_docs):
    specs = pod_specs(default_docs)
    web = specs["itd-invenio-testrig-dashboard-web"]
    (web_container,) = web["containers"]
    (migrate,) = web["initContainers"]
    assert required_keys(web_container) == BASE_KEYS | DISCORD_KEYS | OAUTH_KEYS
    # The OAuth secrets are only for the web server.
    assert required_keys(migrate) == BASE_KEYS | DISCORD_KEYS
    for job in ("dispatch", "ingest", "backup"):
        (container,) = specs[f"itd-invenio-testrig-dashboard-{job}"]["containers"]
        assert required_keys(container) == BASE_KEYS | DISCORD_KEYS, job
        assert container["envFrom"] == [{"secretRef": {"name": SECRET}}]


def test_alerts_disabled_drops_only_the_discord_keys():
    docs = render("--set", "alerts.enabled=false")
    (container,) = pod_specs(docs)["itd-invenio-testrig-dashboard-ingest"]["containers"]
    assert required_keys(container) == BASE_KEYS


def test_the_config_map_is_the_config_values(deployed_docs):
    (config_map,) = by_kind(deployed_docs, "ConfigMap")
    config = yaml.safe_load(config_map["data"]["config.yaml"])
    values = yaml.safe_load(VALUES.read_text())["config"]
    assert config == values


def test_config_changes_restart_the_web_pods():
    def checksum(*args: str) -> str:
        (deployment,) = by_kind(render(*args), "Deployment")
        return deployment["spec"]["template"]["metadata"]["annotations"]["checksum/config"]

    assert checksum() != checksum("--set", "config.lost_after_hours=7")


def test_the_web_container_and_its_probes(default_docs):
    (deployment,) = by_kind(default_docs, "Deployment")
    spec = deployment["spec"]["template"]["spec"]
    (web,) = spec["containers"]
    (migrate,) = spec["initContainers"]
    assert web["args"] == ["web", "--host", "0.0.0.0", "--port", "8000"]  # noqa: S104 - in the pod
    assert migrate["args"] == ["migrate-and-sync", "--wait-for-db", "120"]
    assert "command" not in web  # the image's ENTRYPOINT (tini -- the CLI) stays
    assert web["readinessProbe"]["httpGet"]["path"] == "/api/health"
    # A DB outage must not restart the web pods in a loop.
    assert "tcpSocket" in web["livenessProbe"]
    assert spec["enableServiceLinks"] is False


def test_cronjobs(default_docs):
    jobs = {
        job["metadata"]["name"].rsplit("-", 1)[1]: job["spec"]
        for job in by_kind(default_docs, "CronJob")
    }
    assert jobs.keys() == {"dispatch", "ingest", "backup"}
    for name, job in jobs.items():
        assert job["concurrencyPolicy"] == "Forbid", name
        assert job["timeZone"] == "Etc/UTC", name
        assert job["jobTemplate"]["spec"]["backoffLimit"] == 0, name
        (container,) = job["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        assert container["args"] == [name], name
    # Dispatch is on since the manual dispatch on CERIT worked (2026-11-15); ingest runs.
    assert jobs["dispatch"]["suspend"] is False
    assert jobs["ingest"]["suspend"] is False
    assert jobs["ingest"]["schedule"] == "*/2 * * * *"
    assert jobs["ingest"]["startingDeadlineSeconds"] == 60
    assert jobs["ingest"]["successfulJobsHistoryLimit"] == 1


def test_dispatch_disabled_keeps_a_suspended_cronjob():
    # Suspended, not absent: `kubectl create job --from=cronjob/...` still works for a manual tick.
    (dispatch,) = [
        job
        for job in by_kind(render("--set", "dispatch.enabled=false"), "CronJob")
        if job["metadata"]["name"].endswith("-dispatch")
    ]
    assert dispatch["spec"]["suspend"] is True


def test_dispatch_deadline_grows_with_the_targets(deployed_docs):
    (dispatch,) = [
        job
        for job in by_kind(deployed_docs, "CronJob")
        if job["metadata"]["name"].endswith("-dispatch")
    ]
    # 2 targets x 900 s + 10 min.
    assert dispatch["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] == 2 * 900 + 600


def test_the_nightly_backup(deployed_docs):
    (backup,) = [
        job["spec"]
        for job in by_kind(deployed_docs, "CronJob")
        if job["metadata"]["name"].endswith("-backup")
    ]
    assert backup["schedule"] == "0 3 * * *"
    assert backup["suspend"] is False
    (config_map,) = by_kind(deployed_docs, "ConfigMap")
    config = yaml.safe_load(config_map["data"]["config.yaml"])
    # Its own bucket, never the artifacts one.
    assert config["s3_backup_bucket"] == "invenio-testrig-dashboard-db-backup"
    assert config["s3_backup_bucket"] != config["s3_bucket"]
    assert config["backup_retention_days"] == 30


def test_backup_disabled_renders_no_cronjob():
    names = {
        job["metadata"]["name"]
        for job in by_kind(render("--set", "backup.enabled=false"), "CronJob")
    }
    assert not any(name.endswith("-backup") for name in names)


def test_postgres(default_docs):
    (statefulset,) = by_kind(default_docs, "StatefulSet")
    spec = statefulset["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsUser"] == 999
    assert spec["securityContext"]["fsGroup"] == 999
    (container,) = spec["containers"]
    assert container["image"] == "postgres:18"
    mounts = {mount["mountPath"] for mount in container["volumeMounts"]}
    # The 18+ layout mounts the parent; the socket dir must be writable on a read-only root.
    assert {"/var/lib/postgresql", "/var/run/postgresql", "/tmp", "/dev/shm"} <= mounts  # noqa: S108 - mount paths
    assert required_keys(container) == {"POSTGRES_PASSWORD"}


def test_postgres_disabled_renders_no_database():
    docs = render("--set", "postgres.enabled=false")
    assert not by_kind(docs, "StatefulSet")
    assert [policy["metadata"]["name"] for policy in by_kind(docs, "NetworkPolicy")] == [
        "itd-invenio-testrig-dashboard-web"
    ]


def test_postgres_only_accepts_the_app_pods(default_docs):
    policies = {
        p["metadata"]["name"].rsplit("-", 1)[1]: p["spec"]
        for p in by_kind(default_docs, "NetworkPolicy")
    }
    (rule,) = policies["postgres"]["ingress"]
    labels = rule["from"][0]["podSelector"]["matchLabels"]
    assert labels == {"app.kubernetes.io/instance": "itd", "invenio-testrig-dashboard/app": "true"}
    # Every app pod carries that label, the Postgres pod doesn't.
    for doc in default_docs:
        if doc["kind"] in ("Deployment", "StatefulSet"):
            pod_labels = doc["spec"]["template"]["metadata"]["labels"]
        elif doc["kind"] == "CronJob":
            pod_labels = doc["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
        else:
            continue
        is_app = pod_labels.get("invenio-testrig-dashboard/app") == "true"
        assert is_app == (doc["kind"] != "StatefulSet"), doc["metadata"]["name"]


def test_the_cerit_ingress(deployed_docs):
    (ingress,) = by_kind(deployed_docs, "Ingress")
    # CERIT needs the class in both places, and only letsencrypt-gateway issues (devops.md §9).
    assert ingress["spec"]["ingressClassName"] == "nginx-traefik"
    annotations = ingress["metadata"]["annotations"]
    assert annotations["kubernetes.io/ingress.class"] == "nginx-traefik"
    assert annotations["cert-manager.io/cluster-issuer"] == "letsencrypt-gateway"
    assert ingress["spec"]["tls"] == [
        {"hosts": ["itd.mireksimek.name"], "secretName": "itd-mireksimek-name-tls"}
    ]
    assert ingress["spec"]["rules"][0]["host"] == "itd.mireksimek.name"
    (deployment,) = by_kind(deployed_docs, "Deployment")
    (web,) = deployment["spec"]["template"]["spec"]["containers"]
    assert web["image"].startswith("cerit.io/simekmiroslav/invenio-testrig-dashboard:")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--set", "existingSecret="], "existingSecret is required"),
        (["--set", "config.require_login=false"], "require_login: false"),
        (["--set", "config.testrig_ref=abc"], "testrig_ref was removed"),
        (["--set", "ingress.host="], "ingress.host is required"),
        (["--set", "ingress.tls.secretName="], "ingress.tls.secretName is required"),
    ],
)
def test_the_chart_refuses(args, message):
    assert message in render_error(*args)


def test_the_chart_releases_pyprojects_version(deployed_docs):
    # One version (pyproject.toml) = the image tag = the chart's appVersion; `make
    # bump-version` changes all three, and `make image` refuses a mismatch.
    version = tomllib.loads((CHART.parents[1] / "pyproject.toml").read_text())["project"]["version"]
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    values = yaml.safe_load(VALUES.read_text())
    assert chart["appVersion"] == version
    assert values["image"]["tag"] == version
    (deployment,) = by_kind(deployed_docs, "Deployment")
    (web,) = deployment["spec"]["template"]["spec"]["containers"]
    assert web["image"].endswith(f":{version}")
