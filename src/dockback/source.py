from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "dist",
    "build",
    "coverage",
}


def _git(cwd: Path, *args: str) -> str | None:
    try:
        cp = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
        )
        return cp.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_metadata(context: Path) -> dict[str, Any] | None:
    root = _git(context, "rev-parse", "--show-toplevel")
    if not root:
        return None
    repo_root = Path(root).resolve()
    return {
        "repo_root": str(repo_root),
        "remote_origin": _git(repo_root, "config", "--get", "remote.origin.url"),
        "branch": _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git(repo_root, "rev-parse", "HEAD"),
        "dirty": bool(_git(repo_root, "status", "--porcelain")),
    }


def read_dockerignore(context: Path) -> list[str]:
    path = context / ".dockerignore"
    if not path.is_file():
        return []
    patterns: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _matches_pattern(rel: str, name: str, pattern: str) -> bool:
    # Minimal .dockerignore-compatible matching for common patterns.
    pattern = pattern.strip()
    if not pattern or pattern.startswith("!"):
        return False
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        pattern = pattern[:-1]
    return (
        fnmatch.fnmatch(rel, pattern)
        or fnmatch.fnmatch(name, pattern)
        or fnmatch.fnmatch(rel, f"**/{pattern}")
    )


def should_exclude(path: Path, context: Path, patterns: list[str]) -> bool:
    rel = path.relative_to(context).as_posix()
    parts = set(path.relative_to(context).parts)
    if parts & DEFAULT_EXCLUDES:
        return True
    return any(_matches_pattern(rel, path.name, p) for p in patterns)


def copy_build_context(context: Path, destination: Path) -> None:
    context = context.resolve()
    patterns = read_dockerignore(context)

    def ignore(directory: str, names: list[str]) -> set[str]:
        base = Path(directory)
        excluded: set[str] = set()
        for name in names:
            child = base / name
            try:
                if should_exclude(child, context, patterns):
                    excluded.add(name)
            except ValueError:
                continue
        return excluded

    shutil.copytree(
        context,
        destination,
        symlinks=True,
        dirs_exist_ok=True,
        ignore=ignore,
    )


def write_source_metadata(
    metadata_dir: Path,
    build_entries: list[dict[str, Any]],
) -> None:
    with (metadata_dir / "build.json").open("w", encoding="utf-8") as fh:
        json.dump(build_entries, fh, indent=2)
