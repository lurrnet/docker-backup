from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .discovery import resolved_compose
from .models import DatabaseSpec, MountSpec, ProjectPlan\nfrom .source import copy_build_context, git_metadata, write_source_metadata


def _run(cmd: list[str], *, cwd: Path | None = None, stdout=None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True, stdout=stdout)


def _source_path(project_dir: Path, mount: MountSpec) -> Path:
    source = Path(mount.source)
    if not source.is_absolute():
        source = (project_dir / source).resolve()
    return source


def _copy_bind(project_dir: Path, mount: MountSpec, dest: Path) -> Path | None:
    source = _source_path(project_dir, mount)
    if not source.exists():
        return None
    target = dest / "binds" / mount.service / _safe_name(mount.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    return source


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
            cmd = [
                "docker", "compose", "exec", "-T", db.service,
                "pg_dump", "-Fc", "-U", db.user or "postgres",
                db.database or "postgres",
            ]
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
            _run(
                ["docker", "compose", "exec", "-T", db.service, *env, *args],
                cwd=project_dir,
                stdout=fh,
            )
        return

    raise ValueError(f"Unsupported database type: {db.kind}")


def _backup_sqlite_files(source: Path, dest: Path, service: str) -> list[str]:
    """Create consistent SQLite snapshots in addition to the ordinary bind copy."""
    if not source.exists():
        return []
    candidates: list[Path] = []
    if source.is_file():
        candidates = [source]
    else:
        for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
            candidates.extend(source.rglob(pattern))

    backed_up: list[str] = []
    sqlite_root = dest / "databases" / "sqlite" / service
    for db_path in sorted(set(candidates)):
        try:
            # Header check avoids treating arbitrary .db files as SQLite.
            with db_path.open("rb") as fh:
                if fh.read(16) != b"SQLite format 3\x00":
                    continue
            relative = db_path.name if source.is_file() else str(db_path.relative_to(source))
            target = sqlite_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            dst_conn = sqlite3.connect(target)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
            backed_up.append(str(db_path))
        except (OSError, sqlite3.Error):
            # The regular bind copy still exists; verification/reporting can expose misses.
            continue
    return backed_up


def verify_backup(path: Path) -> tuple[bool, list[str]]:
    problems: list[str] = []
    path = path.expanduser().resolve()
    manifest = path / "metadata" / "manifest.json"
    plan = path / "metadata" / "backup-plan.json"
    if not manifest.is_file():
        problems.append("metadata/manifest.json is missing")
    if not plan.is_file():
        problems.append("metadata/backup-plan.json is missing")
    if manifest.is_file() and manifest.stat().st_size == 0:
        problems.append("manifest is empty")
    if plan.is_file() and plan.stat().st_size == 0:
        problems.append("backup plan is empty")
    for item in (path / "databases").glob("*") if (path / "databases").exists() else []:
        if item.is_file() and item.stat().st_size == 0:
            problems.append(f"empty database dump: {item.name}")
    return not problems, problems


def prepare_backup(plan: ProjectPlan, staging_root: Path) -> Path:
    project_dir = Path(plan.project_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = staging_root.expanduser().resolve() / plan.name / timestamp
    metadata = dest / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)

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

    build_entries = []
    for build in plan.builds:
        context = Path(build.context).resolve()
        source_dest = dest / "source" / build.service
        if context.exists():
            copy_build_context(context, source_dest)
        entry = {
            "service": build.service,
            "context": str(context),
            "dockerfile": build.dockerfile,
            "git": git_metadata(context),
        }
        build_entries.append(entry)
    if build_entries:
        write_source_metadata(metadata, build_entries)

    for db in plan.databases:
        _dump_database(db, dest, project_dir)

    sqlite_snapshots: list[str] = []
    for mount in plan.mounts:
        if mount.action != "backup":
            continue
        if mount.mount_type == "bind":
            source = _copy_bind(project_dir, mount, dest)
            if source:
                sqlite_snapshots.extend(_backup_sqlite_files(source, dest, mount.service))
        elif mount.mount_type == "volume" and mount.source:
            _backup_volume(mount, dest)

    manifest = {
        "project": plan.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format_version": 1,
        "backup_root": str(dest),
        "sqlite_snapshots": sqlite_snapshots,
        "build_sources": [b.service for b in plan.builds],
    }
    with (metadata / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    latest = staging_root.expanduser().resolve() / plan.name / "LATEST"
    latest.write_text(timestamp + "\n", encoding="utf-8")
    return dest


def prune_staging(staging_root: Path, keep: int = 2) -> dict[str, int]:
    """Keep only the newest N completed snapshots per project."""
    staging_root = staging_root.expanduser().resolve()
    removed: dict[str, int] = {}
    if keep < 1:
        raise ValueError("keep must be at least 1")
    if not staging_root.exists():
        return removed

    for project_dir in sorted(p for p in staging_root.iterdir() if p.is_dir()):
        snapshots = sorted(
            [p for p in project_dir.iterdir() if p.is_dir() and p.name[:1].isdigit()],
            key=lambda p: p.name,
            reverse=True,
        )
        count = 0
        for old in snapshots[keep:]:
            shutil.rmtree(old)
            count += 1
        if count:
            removed[project_dir.name] = count
    return removed
