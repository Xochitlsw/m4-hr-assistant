"""Entrega reproducible: CI y Blueprint en la raíz, imagen y locks fijados (ADR 0015).

Estas pruebas leen archivos del repositorio, no ejecutan nada: el pipeline real corre
en GitHub Actions y la imagen la construyen el trabajo ``docker`` y Render.
"""

import re
from pathlib import Path

import yaml

PRODUCT = Path(__file__).resolve().parents[1]
ROOT = PRODUCT.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
BLUEPRINT = ROOT / "render.yaml"
RUNTIME_LOCK = PRODUCT / "requirements.txt"
DEV_LOCK = PRODUCT / "requirements-dev.txt"
RUNTIME = {"fastapi", "uvicorn", "pydantic", "langsmith", "httpx"}
DEV_ONLY = {
    "pytest",
    "pytest-bdd",
    "pytest-cov",
    "pylint",
    "playwright",
    "pytest-playwright",
    "pip-audit",
    "pyyaml",
}
PRODUCT_STAGES = ("style", "unit", "e2e", "docker")
FACTORY_STEPS = ("unit", "contract", "qa", "e2e", "evaluate")
PINNED_ACTION = re.compile(r"uses:\s+[\w.-]+/[\w.-]+@[0-9a-f]{40}\s+#\s+v\d+\.\d+\.\d+")
PINNED_BASE = re.compile(r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$", re.MULTILINE)


def normalize(name: str) -> str:
    """Nombre de paquete como lo compara pip (minúsculas y guiones)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def locked(path: Path) -> dict[str, dict]:
    """Paquetes de un lock: ``{nombre: {"pin": versión, "hashes": cantidad}}``."""
    packages: dict[str, dict] = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        requirement = re.match(r"^([A-Za-z0-9._-]+)==([^\s;\\]+)", line)
        if requirement:
            current = normalize(requirement.group(1))
            packages[current] = {"pin": requirement.group(2), "hashes": 0}
        elif current and line.strip().startswith("--hash=sha256:"):
            packages[current]["hashes"] += 1
    return packages


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def run_lines(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_ci_and_blueprint_live_at_the_repository_root():
    assert WORKFLOW.is_file() and BLUEPRINT.is_file()
    assert not (PRODUCT / ".github").exists()
    assert not (PRODUCT / "render.yaml").exists()


def test_every_action_is_pinned_to_a_commit_with_its_version():
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = [line.strip() for line in text.splitlines() if "uses:" in line]
    assert uses
    assert [line for line in uses if not PINNED_ACTION.search(line)] == []


def test_the_product_stages_are_chained_and_run_inside_product():
    pipeline = workflow()
    assert pipeline["permissions"] == {"contents": "read"}
    assert pipeline["defaults"]["run"]["working-directory"] == "product"
    jobs = pipeline["jobs"]
    for before, after in zip(PRODUCT_STAGES, PRODUCT_STAGES[1:]):
        assert jobs[after]["needs"] == before


def test_the_factory_job_runs_the_factory_checks_from_the_root():
    job = workflow()["jobs"]["factory"]
    assert job["defaults"]["run"]["working-directory"] == "."
    commands = run_lines(job)
    assert "python scripts/verify.py" in commands
    for step in FACTORY_STEPS:
        assert f"python -m verification {step}" in commands


def test_the_docker_job_proves_the_app_cannot_write_its_code_or_data():
    commands = run_lines(workflow()["jobs"]["docker"])
    for folder in ("/app/app", "/app/data"):
        assert f"touch {folder}/" in commands


def test_the_security_job_audits_dependencies_and_scans_for_secrets():
    job = workflow()["jobs"]["security"]
    commands = run_lines(job)
    assert "pip_audit" in commands and "requirements.txt" in commands
    assert any("gitleaks" in step.get("uses", "") for step in job["steps"])
    checkout = job["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0
    assert (ROOT / ".gitleaks.toml").is_file()


def test_ci_installs_only_hashed_locks():
    for job in workflow()["jobs"].values():
        for line in run_lines(job).splitlines():
            if "pip install" in line:
                assert "--require-hashes" in line, line
                assert "requirements" in line, line


def test_the_blueprint_builds_the_product_folder_safely():
    [service] = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))["services"]
    expected = {
        "type": "web",
        "name": "m4-hr-assistant",
        "runtime": "docker",
        "rootDir": "product",
        "plan": "free",
        "healthCheckPath": "/health",
        "autoDeployTrigger": "checksPass",
    }
    assert {key: service[key] for key in expected} == expected
    variables = {item["key"]: item for item in service["envVars"]}
    for secret in ("API_KEYS", "CF_ACCOUNT_ID", "CF_API_TOKEN"):
        assert variables[secret] == {"key": secret, "sync": False}
    assert variables["ENVIRONMENT"]["value"] == "production"
    assert variables["HIDE_INPUTS"]["value"] == "redact"


def test_development_and_production_trace_to_different_projects():
    [service] = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))["services"]
    variables = {item["key"]: item.get("value") for item in service["envVars"]}
    assert variables["LANGSMITH_PROJECT"] == "m4-hr-assistant-prod"
    template = (PRODUCT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "LANGSMITH_PROJECT=m4-hr-assistant-dev" in template


def test_runtime_dependencies_are_pinned_hashed_and_free_of_dev_tools():
    packages = locked(RUNTIME_LOCK)
    assert RUNTIME <= set(packages)
    assert not DEV_ONLY & set(packages)
    assert all(package["hashes"] for package in packages.values())


def test_dev_dependencies_add_the_tools_at_the_same_runtime_versions():
    runtime, dev = locked(RUNTIME_LOCK), locked(DEV_LOCK)
    assert DEV_ONLY <= set(dev)
    assert all(package["hashes"] for package in dev.values())
    assert {name: dev[name]["pin"] for name in runtime} == {
        name: package["pin"] for name, package in runtime.items()
    }


def test_the_image_installs_only_the_runtime_lock_and_runs_unprivileged():
    dockerfile = (PRODUCT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.txt ." in dockerfile
    assert "--require-hashes" in dockerfile and "--no-deps" in dockerfile
    assert "requirements-dev" not in dockerfile
    assert "USER app" in dockerfile and "HEALTHCHECK" in dockerfile
    assert PINNED_BASE.search(dockerfile)
    ignored = (PRODUCT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    excluded = {".env", "tests/", "requirements-dev.txt", "requirements*.in"}
    assert excluded <= set(ignored)
