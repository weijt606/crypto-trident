"""Query known vulnerabilities for every pinned third-party package in uv.lock.

Includes dependencies for other platforms. Does not install or resolve project packages.
Requires pip-audit 2.10.1 (CI installs it); network failures fail the check.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    lock = tomllib.loads((root / 'uv.lock').read_text())
    packages = [p for p in lock['package'] if 'registry' in p.get('source', {})]
    with tempfile.TemporaryDirectory(prefix='trident-audit-') as tmp:
        requirements = Path(tmp) / 'requirements.txt'
        requirements.write_text(''.join(f"{p['name']}=={p['version']}\n" for p in packages))
        return subprocess.call([sys.executable, '-m', 'pip_audit', '-r', str(requirements),
                                '--no-deps', '--disable-pip', '--progress-spinner', 'off'])


if __name__ == '__main__':
    sys.exit(main())
