"""Daily directory-only index from existing local LitePan/bridge records; no HTTP."""
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import time

INDEX_PATH = Path(os.getenv('AVECOVE_DIRECTORY_INDEX', '/opt/docker/avecove-namer/directory-index.json'))


def build_index(state, roots):
    directories = state.get('directories', {})
    rows, seen = {}, set()
    pending = [(str(root['account']), str(root['parent']), root['path'], root['kind']) for root in roots]
    while pending:
        account, parent, path, kind = pending.pop()
        if (account, parent) in seen:
            continue
        seen.add((account, parent))
        folder = directories.get(account + ':' + parent, {})
        for item in folder.get('items', []):
            name = str(item.get('name') or '')
            if not item.get('is_dir') or not item.get('id') or name in {'', '.', '..'} or '/' in name:
                continue
            child = str(PurePosixPath(path) / name)
            rows[child] = {'name': name, 'path': child, 'provider': PurePosixPath(child).parts[1], 'kind': kind}
            pending.append((account, str(item['id']), child, kind))
    return {'version': 1, 'generatedAt': int(time.time()), 'sourceUpdatedAt': state.get('savedAt'),
            'scope': 'cached-directories-only', 'items': sorted(rows.values(), key=lambda row: row['path'])}


def main():
    base = Path('/opt/docker/avecove-namer')
    mappings = json.loads((base / 'secrets/litepan-roots.json').read_text())
    state = json.loads(Path('/opt/banwugan-litepan-bridge/state.json').read_text())
    roots = []
    with sqlite3.connect('file:/opt/docker/litepan/data/litepan.db?mode=ro', uri=True) as db:
        for account, provider, path, parent in db.execute(
                "SELECT t.account_id,a.name,t.path,t.parent_id FROM strm_tasks t JOIN cloud_accounts a ON a.id=t.account_id WHERE t.status='active' AND a.is_active=1"):
            if provider not in {'115', '123', 'Baidu', 'GuangYa'} or provider not in mappings:
                continue
            source, prefix = PurePosixPath(path), PurePosixPath(mappings[provider])
            if not source.is_relative_to(prefix):
                continue
            virtual = PurePosixPath('/') / provider / source.relative_to(prefix)
            if not any(part in {'00影', '00剧', '00漫', '00综艺'} for part in virtual.parts):
                continue
            roots.append({'account': account, 'parent': parent, 'path': str(virtual),
                          'kind': 'movie' if '00影' in virtual.parts else 'tv'})
    if not roots:
        raise ValueError('No configured directory roots; previous index retained')
    result = build_index(state, roots)
    if not result['items']:
        raise ValueError('Empty source; previous index retained')
    temp = INDEX_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    temp.chmod(0o600)
    temp.replace(INDEX_PATH)
    print('Directory index updated:', len(result['items']))


if __name__ == '__main__':
    main()
