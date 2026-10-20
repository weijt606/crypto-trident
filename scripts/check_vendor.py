"""Verify vendored asset hashes; --online also checks npm packages against OSV."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--online', action='store_true')
    args = ap.parse_args()
    manifest = json.loads((ROOT / 'monitor/static/vendor.json').read_text())
    for name, want in manifest['files'].items():
        path = ROOT / name
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError('vendor path outside project')
        if hashlib.sha256(path.read_bytes()).hexdigest() != want:
            raise ValueError(f'vendor integrity mismatch: {name}')
    print('Vendor file integrity verified')
    if args.online:
        for package in [manifest, *manifest.get('bundled_dependencies', [])]:
            payload = {'package': {'name': package['name'], 'ecosystem': 'npm'},
                       'version': package['version']}
            req = urllib.request.Request('https://api.osv.dev/v1/query',
                                         data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read(2 * 1024 * 1024))
            # OSV can retain withdrawn advisories; these are not active findings.
            vulns = [v for v in result.get('vulns', []) if not v.get('withdrawn')]
            if vulns:
                print(f"{package['name']} {package['version']}: " +
                      ', '.join(v['id'] for v in vulns), file=sys.stderr)
                return 1
            print(f"No known vulnerabilities: {package['name']} {package['version']}")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'Vendor check failed ({type(exc).__name__}): {exc}', file=sys.stderr)
        sys.exit(1)
