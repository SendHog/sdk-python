"""Public mirror integrity check; standard library only, no credentials/network."""
import hashlib
import json
import pathlib
import re
import subprocess
import tomllib

root = pathlib.Path(__file__).resolve().parent.parent
source = json.loads((root / 'SOURCE.json').read_text())
package = tomllib.loads((root / 'pyproject.toml').read_text())['project']
if source.get('formatVersion') != 1 or source.get('language') != 'python' or source.get('packageName') != 'sendhog-sdk' or package['name'] != source['packageName'] or package['version'] != source['version'] or not re.fullmatch('[a-f0-9]{40}', source['sourceCommit']):
    raise SystemExit('Invalid release metadata')
for name, digest in source['files'].items():
    path = pathlib.PurePosixPath(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise SystemExit('Unsafe source manifest entry')
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
        raise SystemExit('Export file changed: ' + name)
release = json.loads((root / 'sendhog' / '_release.json').read_text())
if any(release[k] != source[k] for k in ('version', 'sourceCommit', 'contractSha256')):
    raise SystemExit('Wheel provenance mismatch')
if (root / '.git').exists():
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    if any(name and name != 'SOURCE.json' and name not in source['files'] for name in tracked):
        raise SystemExit('Unmanifested tracked file')
print(f"Verified {len(source['files'])} exported files; source {source['sourceCommit']}.")
