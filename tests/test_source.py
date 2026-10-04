from pathlib import Path

from dockback.source import read_dockerignore, should_exclude


def test_default_generated_dirs_are_excluded(tmp_path: Path):
    context = tmp_path
    target = context / "node_modules" / "pkg"
    target.parent.mkdir()
    assert should_exclude(target.parent, context, []) is True


def test_dockerignore_patterns_are_loaded(tmp_path: Path):
    (tmp_path / ".dockerignore").write_text("# comment\n*.log\ncache/\n", encoding="utf-8")
    patterns = read_dockerignore(tmp_path)
    assert "*.log" in patterns
    assert "cache/" in patterns


def test_dockerignore_pattern_matches(tmp_path: Path):
    p = tmp_path / "debug.log"
    p.write_text("x", encoding="utf-8")
    assert should_exclude(p, tmp_path, ["*.log"]) is True
