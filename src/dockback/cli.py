from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backup import prepare_backup, prune_staging, verify_backup
from .discovery import discover_compose_projects, plan_project


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dockback")
    p.add_argument("--root", default="/home", help="root to scan for Compose projects")
    p.add_argument(
        "--staging",
        default="/var/backups/dockback",
        help="staging directory readable by the pull account",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="list Compose projects")

    inspect_p = sub.add_parser("inspect", help="show backup plan")
    inspect_p.add_argument("project_dir")

    backup_p = sub.add_parser("backup", help="prepare backup staging")
    backup_p.add_argument("project_dir", nargs="?")
    backup_p.add_argument("--all", action="store_true")

    verify_p = sub.add_parser("verify", help="verify one prepared backup directory")
    verify_p.add_argument("backup_dir")

    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = Path(args.root)
    staging = Path(args.staging)

    if args.command == "discover":
        for item in discover_compose_projects(root):
            print(item)
        return 0

    if args.command == "inspect":
        plan = plan_project(Path(args.project_dir))
        print(json.dumps(plan.public_dict(), indent=2))
        return 0

    if args.command == "backup":
        if args.all:
            projects = discover_compose_projects(root)
        elif args.project_dir:
            projects = [Path(args.project_dir)]
        else:
            print("backup requires PROJECT_DIR or --all", file=sys.stderr)
            return 2

        failures = 0
        for project in projects:
            try:
                plan = plan_project(project)
                dest = prepare_backup(plan, staging)
                ok, problems = verify_backup(dest)
                if not ok:
                    failures += 1
                    print(f"{plan.name}: verification failed: {'; '.join(problems)}", file=sys.stderr)
                else:
                    print(f"{plan.name}: {dest}")
            except Exception as exc:
                failures += 1
                print(f"{project}: ERROR: {exc}", file=sys.stderr)
        return 1 if failures else 0

    if args.command == "verify":
        ok, problems = verify_backup(Path(args.backup_dir))
        if ok:
            print("OK")
            return 0
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
