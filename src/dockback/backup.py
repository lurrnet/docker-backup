from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .discovery import resolved_compose
from .models import DatabaseSpec, MountSpec, ProjectPlan


def _run(cmd: list[str], *, cwd: Path | None = None, stdout=None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True, stdout=stdout)


def _copy_bind(project_dir: Path, mount: MountSpec, dest: Path) -> None:
    source = Path(mount.source)
    if not source.is_absolute():
        source = (project_dir / source).resolve()
    if not source.exists():
        return
    target = dest / "binds" / mount.service / _safe_name(mount.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)


def _safe_name(value: str) -> str:
    return value.strip("/").replace("/", "__") or "root"


def _backup_volume(mount: MountSpec, dest: Path) -> None:
    out = dest / "volumes"
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"{mount.source}.tar.gz"
    _run([
        "docker", "run", "--rm",
        "-v", f"{mount.source}:/source:ro",
        "-v", f"{out.resolve()}:/backup",
        "alpine:3.20",
        "tar", "czf", f"/backup/{archive.name}", "-C", "/source", ".",
    ])


def _dump_database(db: DatabaseSpec, dest: Path, project_dir: Path) -> None:
    out = dest / "databases"
    out.mkdir(parents=True, exist_ok=True)

    if db.kind == "postgres":
        target = out / f"{db.service}.dump"
        with target.open("wb") as fh:
            cmd = ["docker", "compose", "exec", "-T", db.service,
                   "pg_dump", "-Fc", "-U", db.user or "postgres",
                   db.database or "postgres"]
            _run(cmd, cwd=project_dir, stdout=fh)
        return

    if db.kind in {"mysql", "mariadb"}:
        target = out / f"{db.service}.sql"
        env = []
        if db.password:
            env = ["env", f"MYSQL_PWD={db.password}"]
        dump = "mariadb-dump" if db.kind == "mariadb" else "mysqldump"
        args = [dump, "-u", db.user or "root"]
        if db.database:
            args += ["--databases", db.database]
        else:
            args += ["--all-databases"]
        with target.open("wb") as fh:
            _run(["docker", "compose", "exec", "-T", db.service, *env, *args],
                 cwd=project_dir, stdout=fh)
        return

    raise ValueError(f"Unsupported database type: {db.kind}")


def prepare_backup(plan: ProjectPlan, staging_root: Path) -> Path:
    project_dir = Path(plan.project_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = staging_root.expanduser().resolve() / plan.name / timestamp
    metadata = dest / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)

    # Preserve source compose files and .env for disaster recovery.
    compose_dest = metadata / "compose"
    compose_dest.mkdir(parents=True, exist_ok=True)
    for item in plan.compose_files:
        src = Path(item)
        if src.exists():
            shutil.copy2(src, compose_dest / src.name)
    env_file = project_dir / ".env"
    if env_file.exists():
        shutil.copy2(env_file, compose_dest / ".env")

    with (metadata / "compose.resolved.json").open("w", encoding="utf-8") as fh:
        json.dump(resolved_compose(project_dir), fh, indent=2)
    with (metadata / "backup-plan.json").open("w", encoding="utf-8") as fh:
        json.dump(plan.public_dict(), fh, indent=2)

    for db in plan.databases:
        _dump_database(db, dest, project_dir)

    for mount in plan.mounts:
        if mount.action != "backup":
            continue
        if mount.mount_type == "bind":
            _copy_bind(project_dir, mount, dest)
        elif mount.mount_type == "volume" and mount.source:
            _backup_volume(mount, dest)

    manifest = {
        "project": plan.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format_version": 1,
        "backup_root": str(dest),
    }
    with (metadata / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    latest = staging_root.expanduser().resolve() / plan.name / "LATEST"
    latest.write_text(timestamp + "\n", encoding="utf-8")
    return dest
