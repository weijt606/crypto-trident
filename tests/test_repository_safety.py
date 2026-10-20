"""Privacy gate checks the staged snapshot without following external files."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_repository_safety.py"
spec = importlib.util.spec_from_file_location("repository_safety", SCRIPT)
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


@pytest.mark.parametrize("path", [
    ".env", "nested/.env.production", "config/bots/demo.local.yaml",
    "custom/live.sqlite-wal", "custom/session.jsonl", "nested/.claude/session.md",
    "logs/output.txt", "config/capital_events.yaml", "custom/test_state.json",
])
def test_private_files_rejected_even_without_secret_content(path):
    assert "private-file" in safety.problems_for(path, "100644", b"harmless")


def test_examples_and_documentation_paths_allowed():
    assert not safety.problems_for(".env.example", "100644", b"API_KEY=\n")
    assert not safety.problems_for("AGENTS.md", "100644", b"Use /home/<user>/project")


@pytest.mark.parametrize("target", [b"../../outside", b"/etc/passwd", b"C:\\private"])
def test_external_symlink_rejected_without_reading_target(target):
    assert "external-symlink" in safety.problems_for("docs/link", "120000", target)
    assert not safety.problems_for("docs/link", "120000", b"../README.md")


def test_personal_machine_path_rejected():
    private_path = b"/Users/" + b"example-person/private-project"
    assert "personal-machine-path" in safety.problems_for("README.md", "100644", private_path)


def test_gate_reads_index_not_replaced_worktree(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    p = tmp_path / "README.md"
    p.write_bytes(b"/Users/" + b"example-person/private-project")
    git("add", "README.md")
    p.write_text("Clean worktree does not clean the staged snapshot.\n")
    checked, counts = safety.check_index(tmp_path)
    assert checked == 1
    assert counts == {"personal-machine-path": 1}
    git("add", "README.md")
    assert safety.check_index(tmp_path) == (1, {})
