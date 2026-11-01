"""Test, publish the committed source over Git SSH, and deploy that exact revision.

Uses existing SSH configuration; never uploads local secrets or runtime data.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='Existing SSH host alias')
    parser.add_argument('--url', default='https://my.avecrouge.top/media-tools')
    args = parser.parse_args()
    if not args.host.replace('-', '').replace('_', '').isalnum():
        parser.error('Use an SSH host alias, not a shell expression')
    if run('git', 'status', '--porcelain', stdout=subprocess.PIPE).stdout:
        raise SystemExit('Commit intended changes and preserve unrelated files before publishing.')
    run(sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q',
        env={**__import__('os').environ, 'PYTHONPATH':str(ROOT / 'src')})
    run('node', 'tests/test_web_ui.mjs')
    sha = run('git', 'rev-parse', 'HEAD', text=True, stdout=subprocess.PIPE).stdout.strip()
    run('git', 'push', 'origin', 'HEAD:main')
    with tempfile.TemporaryDirectory(prefix='namer-release-') as directory:
        archive = Path(directory) / (sha + '.tar')
        run('git', 'archive', '--format=tar', '-o', str(archive), sha)
        run('scp', str(archive), f'{args.host}:/tmp/namer-{sha}.tar')
        run('ssh', args.host, 'python3', '-', sha, input=REMOTE, text=True)
    with urllib.request.urlopen(args.url.rstrip('/') + '/api/health', timeout=20) as response:
        health = json.load(response)
    if health.get('revision') != sha:
        raise SystemExit('Public revision mismatch: inspect reverse proxy; do not claim completion.')
    print(json.dumps(health, ensure_ascii=False))


REMOTE = r'''
import json, os, pathlib, shutil, subprocess, sys, tarfile, time, urllib.request
sha = sys.argv[1]
assert len(sha) == 40 and all(c in '0123456789abcdef' for c in sha)
base = pathlib.Path('/opt/docker/avecove-namer')
current = base / 'current'
old = current.resolve()
assert current.is_symlink(), 'Current must be a managed release symlink'
with urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=10) as response:
    health = json.load(response)
assert 'activeJobs' in health, 'Install the version-aware health endpoint before using this publisher'
assert health['activeJobs'] == 0, 'A job is running; finish it before deployment'
target = base / 'releases' / sha
assert not target.exists(), 'Release already exists; use a new commit, never overwrite a release'
backup = base / 'backups' / ('release-' + time.strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True)
shutil.copytree(old, backup / 'source')
for name in ('series-shelf.json','rename-rates.json','scout-roots.json'):
    path = base / name
    if path.is_file(): shutil.copy2(path, backup / name)
inbox = base / 'handoff-inbox.sqlite3'
if inbox.is_file():
    import sqlite3
    with sqlite3.connect('file:' + str(inbox) + '?mode=ro', uri=True) as source_db:
        with sqlite3.connect(backup / inbox.name) as backup_db:
            source_db.backup(backup_db)
    (backup / inbox.name).chmod(0o600)
target.mkdir(parents=True)
with tarfile.open('/tmp/namer-' + sha + '.tar') as archive:
    for entry in archive.getmembers():
        assert not entry.issym() and not entry.islnk()
        assert (target / entry.name).resolve().is_relative_to(target)
    archive.extractall(target)
(target / '.deployed-commit').write_text(sha + '\n')
subprocess.run(['python3','-m','compileall','-q',str(target / 'src')], check=True)
link = base / ('current-' + sha)
link.symlink_to(target)
os.replace(link, current)
try:
    subprocess.run(['systemctl','restart','avecove-namer-web'], check=True)
    for attempt in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=3) as response:
                ready = json.load(response)
            if ready.get('revision') == sha and ready.get('status') == 'ok': break
        except Exception: pass
        time.sleep(1)
    else: raise RuntimeError('New release failed health verification')
except Exception:
    link.symlink_to(old)
    os.replace(link, current)
    subprocess.run(['systemctl','restart','avecove-namer-web'], check=True)
    raise
print(json.dumps({'revision':sha,'backup':str(backup),'previous':str(old)}))
'''

if __name__ == '__main__':
    main()
