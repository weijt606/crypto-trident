"""Check indexed blobs for private files, machine paths and escaping symlinks.

Reads Git's index, never .env, untracked runtime data or an external symlink target.
This complements gitleaks; it is not a general secret detector or legal approval.
"""
from __future__ import annotations

import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_DIRS = {"storage", "logs", ".claude", ".codex", ".agent-runs"}
PERSONAL_PATH = re.compile(
    rb"(?:/Users/|/home/)[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+\\"
)


def problems_for(path: str, mode: str, content: bytes) -> set[str]:
    p = PurePosixPath(path)
    name = p.name.lower()
    problems = set()
    if (set(p.parts[:-1]) & PRIVATE_DIRS
            or (name != ".env.example" and (name == ".env" or name.startswith(".env.")
                                           or name.endswith(".env")))
            or name == ".envrc" or name.endswith((".pem", ".key", ".p12", ".pfx"))
            or name in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
            or name.endswith((".local.yaml", "_state.json", ".kill", ".softcut",
                              ".parquet", ".db", ".log", ".jsonl"))
            or ".sqlite" in name or path == "config/capital_events.yaml"):
        problems.add("private-file")
    if mode == "120000":
        target = content.decode("utf-8", errors="replace")
        resolved = posixpath.normpath(posixpath.join(str(p.parent), target))
        # Relative in-repo symlinks are allowed; never follow them while scanning.
        if (target.startswith("/") or "\\" in target or re.match(r"^[A-Za-z]:", target)
                or resolved == ".." or resolved.startswith("../")):
            problems.add("external-symlink")
    if b"\0" not in content and PERSONAL_PATH.search(content):
        problems.add("personal-machine-path")
    return problems


def check_index(root: Path = ROOT) -> tuple[int, dict[str, int]]:
    entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root)
    counts: dict[str, int] = {}
    checked = 0
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, oid, stage = meta.decode("ascii").split()
        if stage != "0":
            counts["unmerged-index"] = counts.get("unmerged-index", 0) + 1
            continue
        if mode == "160000":
            counts["unreviewed-submodule"] = counts.get("unreviewed-submodule", 0) + 1
            continue
        content = subprocess.check_output(["git", "cat-file", "blob", oid], cwd=root)
        checked += 1
        for problem in problems_for(raw_path.decode("utf-8", errors="replace"), mode, content):
            counts[problem] = counts.get(problem, 0) + 1
    return checked, counts


def main() -> int:
    try:
        checked, counts = check_index()
    except (OSError, subprocess.CalledProcessError, ValueError):
        print("Repository safety check could not complete; refusing to pass.")
        return 1
    if counts:
        # No matching lines, file names or credentials in local/CI output.
        print("Repository safety check failed (matched content and paths withheld):")
        for rule, count in sorted(counts.items()):
            print(f"  {rule}: {count}")
        return 1
    print(f"Repository safety check passed: {checked} indexed blobs checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
