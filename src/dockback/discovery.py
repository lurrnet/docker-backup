from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .models import BuildSpec, DatabaseSpec, MountSpec, ProjectPlan

COMPOSE_NAMES = (
    "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"
)

CACHE_TOKENS = {"cache", "tmp", "temp", "logs", "log", "runtime", "run"}
DATA_TOKENS = {
    "data", "config", "configs", "storage", "uploads", "upload",
    "media", "documents", "files", "library", "backup", "backups"
}


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=True,
    )


def discover_compose_projects(root: Path) -> list[Path]:
    found: dict[Path, Path] = {}
    root = root.expanduser().resolve()
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "__pycache__"}]
        names = set(files)
        for candidate in COMPOSE_NAMES:
            if candidate in names:
                p = Path(current).resolve()
                found[p] = p / candidate
                break
    return sorted(found)


def compose_files(project_dir: Path) -> list[Path]:
    return [project_dir / name for name in COMPOSE_NAMES if (project_dir / name).is_file()]


def resolved_compose(project_dir: Path) -> dict[str, Any]:
    cp = run(["docker", "compose", "config", "--format", "json"], cwd=project_dir)
    return json.loads(cp.stdout)


def _env(service: dict[str, Any]) -> dict[str, str]:
    value = service.get("environment") or {}
    if isinstance(value, dict):
        return {str(k): "" if v is None else str(v) for k, v in value.items()}
    result: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                result[k] = v
    return result


def detect_database(service_name: str, service: dict[str, Any]) -> DatabaseSpec | None:
    image = str(service.get("image") or "").lower()
    env = _env(service)

    if any(x in image for x in ("postgres", "postgis", "timescale")):
        return DatabaseSpec(
            service=service_name,
            kind="postgres",
            database=env.get("POSTGRES_DB") or "postgres",
            user=env.get("POSTGRES_USER") or "postgres",
            password=env.get("POSTGRES_PASSWORD"),
            container_data_paths=["/var/lib/postgresql/data"],
        )

    if "mariadb" in image:
        return DatabaseSpec(
            service=service_name,
            kind="mariadb",
            database=env.get("MARIADB_DATABASE") or env.get("MYSQL_DATABASE"),
            user=env.get("MARIADB_USER") or env.get("MYSQL_USER") or "root",
            password=env.get("MARIADB_PASSWORD") or env.get("MARIADB_ROOT_PASSWORD")
            or env.get("MYSQL_PASSWORD") or env.get("MYSQL_ROOT_PASSWORD"),
            container_data_paths=["/var/lib/mysql"],
        )

    if image.startswith("mysql") or "/mysql" in image:
        return DatabaseSpec(
            service=service_name,
            kind="mysql",
            database=env.get("MYSQL_DATABASE"),
            user=env.get("MYSQL_USER") or "root",
            password=env.get("MYSQL_PASSWORD") or env.get("MYSQL_ROOT_PASSWORD"),
            container_data_paths=["/var/lib/mysql"],
        )

    return None


def _path_tokens(value: str) -> set[str]:
    normalized = value.replace("\\", "/").lower()
    chunks = []
    for part in normalized.split("/"):
        chunks.extend(part.replace("-", "_").split("_"))
    return {x for x in chunks if x}


def classify_mount(service: str, mount: dict[str, Any], db: DatabaseSpec | None) -> MountSpec:
    mtype = str(mount.get("type") or "volume")
    source = str(mount.get("source") or "")
    target = str(mount.get("target") or "")
    tokens = _path_tokens(source + "/" + target)

    if db and target in db.container_data_paths:
        return MountSpec(service, mtype, source, target, "database", "logical-dump",
                         f"{db.kind} data directory")

    if tokens & CACHE_TOKENS:
        return MountSpec(service, mtype, source, target, "cache", "skip",
                         "cache/tmp/log-like mount")

    if tokens & DATA_TOKENS:
        return MountSpec(service, mtype, source, target, "application-data", "backup")

    return MountSpec(service, mtype, source, target, "unknown", "backup",
                     "unknown persistent mount; conservative default")


def _resolve_volume_source(config: dict[str, Any], mount: dict[str, Any]) -> dict[str, Any]:
    """Replace a Compose volume key with Docker's effective volume name when available."""
    if str(mount.get("type") or "") != "volume":
        return mount
    source = str(mount.get("source") or "")
    if not source:
        return mount
    volume_def = (config.get("volumes") or {}).get(source) or {}
    effective = volume_def.get("name")
    if not effective:
        return mount
    resolved = dict(mount)
    resolved["source"] = str(effective)
    return resolved


def plan_project(project_dir: Path) -> ProjectPlan:
    project_dir = project_dir.expanduser().resolve()
    config = resolved_compose(project_dir)
    services_cfg = config.get("services") or {}

    databases: list[DatabaseSpec] = []
    service_db: dict[str, DatabaseSpec] = {}
    services: list[dict[str, Any]] = []
    mounts: list[MountSpec] = []\n    builds: list[BuildSpec] = []

    for name, service in services_cfg.items():
        db = detect_database(name, service)
        if db:
            databases.append(db)
            service_db[name] = db
        build = service.get("build")
        if isinstance(build, dict):
            context = str(build.get("context") or ".")
            dockerfile = build.get("dockerfile")
            context_path = Path(context)
            if not context_path.is_absolute():
                context_path = (project_dir / context_path).resolve()
            builds.append(BuildSpec(
                service=name,
                context=str(context_path),
                dockerfile=str(dockerfile) if dockerfile else None,
            ))
        services.append({
            "name": name,
            "image": service.get("image"),
            "container_name": service.get("container_name"),
            "build": build,
        })

    for name, service in services_cfg.items():
        for mount in service.get("volumes") or []:
            if isinstance(mount, dict):
                mount = _resolve_volume_source(config, mount)
                mounts.append(classify_mount(name, mount, service_db.get(name)))

    warnings: list[str] = []
    if not mounts:
        warnings.append("No persistent mounts detected.")

    return ProjectPlan(
        name=str(config.get("name") or project_dir.name),
        compose_files=[str(x) for x in compose_files(project_dir)],
        project_dir=str(project_dir),
        services=services,
        databases=databases,
        mounts=mounts,
        warnings=warnings,
    )
