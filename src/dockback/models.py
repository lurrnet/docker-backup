from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DatabaseSpec:
    service: str
    kind: str
    database: str | None = None
    user: str | None = None
    password: str | None = None
    container_data_paths: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["password"] = "***" if self.password else None
        return data


@dataclass
class MountSpec:
    service: str
    mount_type: str
    source: str
    target: str
    category: str
    action: str
    reason: str = ""


@dataclass
class BuildSpec:
    service: str
    context: str
    dockerfile: str | None = None


@dataclass
class ProjectPlan:
    name: str
    compose_files: list[str]
    project_dir: str
    services: list[dict[str, Any]]
    databases: list[DatabaseSpec]
    mounts: list[MountSpec]
    builds: list[BuildSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "compose_files": self.compose_files,
            "project_dir": self.project_dir,
            "services": self.services,
            "databases": [db.public_dict() for db in self.databases],
            "mounts": [asdict(m) for m in self.mounts],
            "builds": [asdict(b) for b in self.builds],
            "warnings": self.warnings,
        }

    @property
    def path(self) -> Path:
        return Path(self.project_dir)
