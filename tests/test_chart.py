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
    return specs


def web_deployment(docs: list[dict]) -> dict:
    (deployment,) = [
        d for d in by_kind(docs, "Deployment") if d["metadata"]["name"].endswith("-web")
    ]
    return deployment


def containers(spec: dict) -> list[dict]:
    return spec.get("initContainers", []) + spec["containers"]


def crontab(docs: list[dict]) -> list[str]:
    """The scheduler's job lines (comments dropped)."""
    (config_map,) = by_kind(docs, "ConfigMap")
    lines = config_map["data"]["crontab"].splitlines()
    return [line for line in lines if line.strip() and not line.startswith("#")]


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


SCHEDULER = "itd-invenio-testrig-dashboard-scheduler"


def test_every_pod_passes_the_restricted_pod_security_standard(default_docs, deployed_docs):
    # web, postgres, scheduler.
    for docs, count in ((default_docs, 3), (deployed_docs, 3)):
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
    (scheduler,) = specs[SCHEDULER]["containers"]
    assert required_keys(scheduler) == BASE_KEYS | DISCORD_KEYS
    assert scheduler["envFrom"] == [{"secretRef": {"name": SECRET}}]


def test_alerts_disabled_drops_only_the_discord_keys():
    docs = render("--set", "alerts.enabled=false")
    (container,) = pod_specs(docs)[SCHEDULER]["containers"]
    assert required_keys(container) == BASE_KEYS


def test_the_config_map_is_the_config_values(deployed_docs):
    (config_map,) = by_kind(deployed_docs, "ConfigMap")
    config = yaml.safe_load(config_map["data"]["config.yaml"])
    values = yaml.safe_load(VALUES.read_text())["config"]
    assert config == values


def test_config_changes_restart_the_web_pods():
    def checksum(*args: str) -> str:
        deployment = web_deployment(render(*args))
        return deployment["spec"]["template"]["metadata"]["annotations"]["checksum/config"]

    assert checksum() != checksum("--set", "config.lost_after_hours=7")


def test_the_web_container_and_its_probes(default_docs):
    deployment = web_deployment(default_docs)
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


def test_no_cronjobs_one_scheduler_pod(deployed_docs):
    # An ingest every 2 minutes as a CronJob left a finished pod behind every 2 minutes.
    assert by_kind(deployed_docs, "CronJob") == []
    (deployment,) = [
        d for d in by_kind(deployed_docs, "Deployment") if d["metadata"]["name"] == SCHEDULER
    ]
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "Recreate"}  # never two schedulers during an upgrade
    pod = spec["template"]["spec"]
    (container,) = pod["containers"]
    # tini reaps testrig's orphans; supercronic replaces the image's ENTRYPOINT entirely.
    assert container["command"] == [
        "tini",
        "--",
        "supercronic",
        "-passthrough-logs",
        "/etc/invenio-testrig-dashboard/crontab",
    ]
    mounts = {m["mountPath"]: m for m in container["volumeMounts"]}
    assert mounts["/etc/invenio-testrig-dashboard/crontab"]["subPath"] == "crontab"
    # Jobs never start on an old schema: the same migrate initContainer as the web pod.
    (migrate,) = pod["initContainers"]
    assert migrate["args"] == ["migrate-and-sync", "--wait-for-db", "120"]
    assert "checksum/config" in spec["template"]["metadata"]["annotations"]


def test_the_crontab(deployed_docs):
    # 2 targets x 900 s + 10 min for dispatch; every job stopped by `timeout` if it hangs.
    assert crontab(deployed_docs) == [
        "*/15 * * * * timeout -s TERM 2400 invenio-testrig-dashboard dispatch",
        "*/2 * * * * timeout -s TERM 1800 invenio-testrig-dashboard ingest",
        "0 3 * * * timeout -s TERM 3600 invenio-testrig-dashboard backup",
    ]


@pytest.mark.parametrize("job", ["dispatch", "ingest", "backup"])
def test_a_disabled_job_leaves_the_crontab(job):
    lines = crontab(render("--set", f"{job}.enabled=false"))
    assert len(lines) == 2
    assert not any(line.endswith(f" {job}") for line in lines)


def test_no_jobs_no_scheduler_pod():
    docs = render(
        *(
            arg
            for job in ("dispatch", "ingest", "backup")
            for arg in ("--set", f"{job}.enabled=false")
        )
    )
    assert SCHEDULER not in {d["metadata"]["name"] for d in by_kind(docs, "Deployment")}


def test_the_backup_settings(deployed_docs):
    (config_map,) = by_kind(deployed_docs, "ConfigMap")
    config = yaml.safe_load(config_map["data"]["config.yaml"])
    # Its own bucket, never the artifacts one.
    assert config["s3_backup_bucket"] == "invenio-testrig-dashboard-db-backup"
    assert config["s3_backup_bucket"] != config["s3_bucket"]
    assert config["backup_retention_days"] == 30


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
    deployment = web_deployment(deployed_docs)
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
    deployment = web_deployment(deployed_docs)
    (web,) = deployment["spec"]["template"]["spec"]["containers"]
    assert web["image"].endswith(f":{version}")
