"""Durable, local-only acquisition notifications; never accesses cloud files."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import sqlite3
import time


class HandoffInbox:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        self.path.chmod(0o600)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, signature TEXT, payload TEXT, received REAL, acknowledged INTEGER DEFAULT 0)')
            connection.execute('BEGIN IMMEDIATE')
            with connection:
                yield connection
        finally:
            connection.close()

    def receive(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('通知必须为 JSON 对象')
        path = str(payload.get('path') or '').strip()
        parts = PurePosixPath(path).parts
        if (not path.startswith('/') or any(ord(c) < 32 for c in path)
                or path.startswith('//') or '..' in parts or len(path) > 1500 or len(parts) < 5
                or parts[1] not in {'115', '123', 'GuangYa', 'Baidu', 'Quark'}
                or parts[2] not in {'00影', '00剧', '00漫', '00综艺'}):
            raise ValueError('请提交网盘分类下面的具体作品目录，不要提交全盘或分类根目录')
        path = str(PurePosixPath(path))
        kind = payload.get('kind') or ('movie' if parts[2] == '00影' else 'tv')
        if kind not in {'movie', 'tv'}:
            raise ValueError('kind 仅支持 movie / tv')
        revision = str(payload.get('signature') or payload.get('event_id') or 'initial')
        source = str(payload.get('source') or 'skill')
        if len(revision) > 200 or len(source) > 60:
            raise ValueError('通知标识或来源过长')
        if payload.get('ready') is not True:
            raise ValueError('请在资源转存完成后设置 ready=true；下载中目录不接收')
        ident = hashlib.sha256((kind + ':' + path).encode()).hexdigest()
        signature = hashlib.sha256(revision.encode()).hexdigest()
        item = {'id': ident, 'signature': signature, 'path': path, 'kind': kind,
                'provider': parts[1], 'name': parts[-1], 'source': source}
        with self.connection() as connection:
            old = connection.execute('SELECT signature FROM events WHERE id=?', (ident,)).fetchone()
            if old and old['signature'] == signature:
                return {'accepted': False, 'duplicate': True, 'id': ident}
            if connection.execute('SELECT count(*) FROM events WHERE acknowledged=0').fetchone()[0] >= 500:
                raise ValueError('通知收件箱已满，请先审核现有通知')
            connection.execute('INSERT OR REPLACE INTO events VALUES (?,?,?,?,0)',
                               (ident, signature, json.dumps(item, ensure_ascii=False), time.time()))
        return {'accepted': True, 'duplicate': False, 'id': ident}

    def request(self, payload=None):
        with self.connection() as connection:
            if payload is not None:
                items = payload.get('items')
                if not isinstance(items, list) or len(items) > 100 or any(not isinstance(item, dict) for item in items):
                    raise ValueError('每次最多确认 100 条通知')
                for item in items:
                    connection.execute('UPDATE events SET acknowledged=1 WHERE id=? AND signature=?',
                                       (str(item.get('id')), str(item.get('signature'))))
            rows = connection.execute('SELECT payload FROM events WHERE acknowledged=0 ORDER BY received DESC, id LIMIT 30').fetchall()
            pending = connection.execute('SELECT count(*) FROM events WHERE acknowledged=0').fetchone()[0]
        return {'items': [json.loads(row['payload']) for row in rows], 'pending': pending,
                'note': '通知不代表已识别或已改名；加入队列后仍需预览确认'}
