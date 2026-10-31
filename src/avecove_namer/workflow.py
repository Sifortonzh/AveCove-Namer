"""Native LitePan STRM generation followed by targeted Emby notifications."""
from __future__ import annotations

import http.cookiejar
import base64
import uuid
from contextlib import closing
import json
import sqlite3
from pathlib import Path, PurePosixPath
import time
import urllib.parse
import urllib.request


class LitePanWorkflow:
    def __init__(self, settings, report, emby_token, progress=None):
        self.settings, self.report, self.emby_token = settings, report, emby_token
        self.progress = progress or (lambda **fields: None)
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def api(self, path, body=None):
        request = urllib.request.Request(self.settings.litepan_url.rstrip('/') + '/api' + path,
            None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=30) as response:
            payload = json.load(response)
        if not payload.get('success'):
            raise ValueError(payload.get('message') or 'LitePan 请求失败')
        return payload.get('data')

    def login(self):
        form = urllib.parse.urlencode({'username': self.settings.litepan_username,
            'password': self.settings.litepan_password_file.read_text().strip()}).encode()
        with self.opener.open(self.settings.litepan_url.rstrip('/') + '/api/auth/login', form, timeout=20) as response:
            if not json.load(response).get('success'):
                raise ValueError('LitePan 登录失败')

    def match_tasks(self, paths, tasks):
        roots = json.loads(self.settings.litepan_roots_file.read_text())
        account_ids = {'115': 1, '123': 2, 'GuangYa': 3, 'Baidu': 4, 'Quark': 5}
        selected = {}
        for path in paths:
            parts = PurePosixPath(path).parts
            provider = parts[1]
            actual = str(PurePosixPath(roots[provider], *parts[2:]))
            matches = [task for task in tasks if task['account_id'] == account_ids[provider]
                and task.get('status') == 'active'
                and (actual == task['path'].rstrip('/') or actual.startswith(task['path'].rstrip('/') + '/'))]
            if not matches:
                raise ValueError(f'没有对应的 LitePan STRM 任务：{path}')
            task = max(matches, key=lambda item: len(item['path']))
            selected[task['id']] = task
        return list(selected.values())

    def snapshot(self, root):
        base = self.settings.media_index_root.resolve()
        root = root.resolve()
        if base not in root.parents:
            raise ValueError('LitePan STRM 输出目录不安全')
        return {str(p.relative_to(base)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in root.rglob('*.strm') if p.is_file()}

    def invalidate_directory_paths(self, paths, task):
        # Only invalidate derived 115 pid->path cache for requested folders.
        # Native LitePan will resolve and rebuild these rows; no media deleted.
        if task['account_id'] != 1:
            return
        roots = json.loads(self.settings.litepan_roots_file.read_text())
        prefixes = []
        for path in paths:
            parts = PurePosixPath(path).parts
            if parts[1] != '115':
                continue
            actual = str(PurePosixPath(roots['115'], *parts[2:]))
            if actual == task['path'] or actual.startswith(task['path'].rstrip('/') + '/'):
                prefixes.append(actual.lstrip('/'))
        with closing(sqlite3.connect(self.settings.litepan_db_path, timeout=15)) as db, db:
            removed = 0
            for prefix in prefixes:
                rows = db.execute('SELECT dir_id FROM strm_remote_dir_cache WHERE account_id=? AND (ltrim(dir_path,\'/\')=? OR substr(ltrim(dir_path,\'/\'),1,?)=?)',
                    (1, prefix, len(prefix) + 1, prefix + '/')).fetchall()
                db.executemany('DELETE FROM strm_remote_dir_cache WHERE account_id=? AND dir_id=?', [(1, row[0]) for row in rows])
                removed += len(rows)
        self.report(f'已失效 {removed} 条旧目录路径缓存，LitePan 将重新解析当前路径')

    def wait_task(self, task_id, previous=None, allow_protected=False):
        deadline = time.monotonic() + 30 * 60
        while time.monotonic() < deadline:
            task = next(t for t in self.api('/admin/strm/tasks') if t['id'] == task_id)
            finished = not task.get('is_scanning') and (previous is None or task.get('last_scan') != previous)
            if finished:
                if previous is not None and task.get('last_scan_status') != 'ok' and not (allow_protected and task.get('last_scan_status') == 'protected'):
                    raise ValueError(task.get('error_message') or 'LitePan STRM 生成未成功，未通知 Emby')
                return task
            self.report(f"LitePan：{task['name']} · {task.get('current_label') or task.get('scan_phase') or '排队中'} · 已扫描 {task.get('scanned_files', 0)} 个文件")
            time.sleep(3)
        raise TimeoutError('LitePan STRM 等待超时；可单独重试同步，无需再次改名')

    def save_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    @staticmethod
    def file_identity(path):
        if path.is_symlink():
            return None
        try:
            parts = urllib.parse.urlsplit(path.read_text().strip()).path.split('/')
            if parts[1:4] != ['api', 'strm', 'play'] or len(parts) < 9 or parts[6] != 't' or parts[8] != 'n':
                return None
            return (parts[4], parts[5])
        except (OSError, UnicodeError, ValueError):
            return None

    def quarantine_duplicates(self, root, task=None):
        """Same cloud file ID only. Ambiguous timestamps are never auto-removed."""
        groups = {}
        for path in root.rglob('*.strm'):
            key = self.file_identity(path)
            if key:
                groups.setdefault(key, []).append(path)
        quarantine = self.settings.work_root / 'strm-quarantine' / uuid.uuid4().hex
        manifest = []
        listings = {}
        def list_folder(account, folder):
            key = (account, str(folder))
            if key in listings:
                return listings[key]
            if str(folder) == '/':
                parent = '0'
            else:
                siblings = list_folder(account, folder.parent)
                parent = str(next(i['id'] for i in siblings if i.get('is_dir') and i['name'] == folder.name))
            data = self.api('/files/list?' + urllib.parse.urlencode({'account_id': account, 'parent_id': parent, 'force_refresh': 'true'}))
            listings[key] = data.get('items') or []
            time.sleep(0.15)
            return listings[key]
        for key, files in groups.items():
            if len(files) < 2:
                continue
            files.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
            if files[0].stat().st_mtime_ns == files[1].stat().st_mtime_ns:
                continue
            winner = files[0]
            # Validate the retained reference still addresses an existing cloud file.
            try:
                file_id = base64.urlsafe_b64decode(key[1] + '=' * (-len(key[1]) % 4)).decode()
                if task:
                    folder = PurePosixPath(task['path'], *winner.relative_to(root).parent.parts)
                    info = next((i for i in list_folder(key[0], folder) if str(i.get('id')) == file_id), None)
                else:
                    info = self.api('/files/info?' + urllib.parse.urlencode({'account_id': key[0], 'file_id': file_id}))
                if not info or info.get('is_dir') or str(info.get('id')) != file_id:
                    continue
            except Exception:
                continue
            for old in files[1:]:
                relative = str(old.relative_to(self.settings.media_index_root))
                target = quarantine / relative
                manifest.append({'old': relative, 'keep': str(winner.relative_to(self.settings.media_index_root)), 'account_id': key[0], 'file_id': file_id})
                self.save_json(quarantine / 'manifest.json', manifest)
                target.parent.mkdir(parents=True, exist_ok=True)
                old.replace(target)
        if manifest:
            self.report(f'已隔离 {len(manifest)} 个同文件重复 STRM；不同文件／画质完整保留，可从隔离备份恢复')
        return manifest

    def notify_pending(self, updates, pending_file):
        # Each acknowledged batch is removed durably, failures remain retryable.
        updates = list({(u['Path'], u['UpdateType']): u for u in updates}.values())
        self.save_json(pending_file, updates)
        count = len(updates)
        token = self.emby_token() if updates else ''
        while updates:
            batch = updates[:100]
            request = urllib.request.Request(self.settings.emby_url.rstrip('/') + '/emby/Library/Media/Updated',
                json.dumps({'Updates': batch}).encode(), headers={'Content-Type': 'application/json', 'X-Emby-Token': token})
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status not in (200, 204):
                    raise ValueError('Emby 未接受更新通知')
            updates = updates[len(batch):]
            self.save_json(pending_file, updates)
        return count

    def orphan_updates(self, root):
        """Missing local indexes are only unindexed, never delete cloud/Emby files."""
        prefix = '/data/litepan-strm/' + str(root.relative_to(self.settings.media_index_root)) + '/'
        request = urllib.request.Request(self.settings.emby_url.rstrip('/') + '/emby/Library/VirtualFolders',
            headers={'X-Emby-Token': self.emby_token()})
        with urllib.request.urlopen(request, timeout=15) as response:
            libraries = json.load(response)
        scopes = [str(library['ItemId']) for library in libraries
            if any(prefix.startswith(location.rstrip('/') + '/') or location.rstrip('/') == prefix.rstrip('/')
                   for location in library.get('Locations', []))]
        if not scopes:
            raise ValueError('该 STRM 输出目录未挂入 Emby 媒体库，已停止全库查询')
        updates = []
        indexed = set()
        start = 0
        while True:
            query = urllib.parse.urlencode({'ParentId': scopes[0], 'Recursive': 'true', 'IncludeItemTypes': 'Movie,Episode', 'Fields': 'Path', 'EnableImages': 'false', 'EnableUserData': 'false', 'StartIndex': start, 'Limit': 200})
            request = urllib.request.Request(self.settings.emby_url.rstrip('/') + '/emby/Items?' + query,
                headers={'X-Emby-Token': self.emby_token()})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            items = payload.get('Items', [])
            for item in items:
                path = item.get('Path', '')
                if path.startswith(prefix) and path.endswith('.strm'):
                    indexed.add(path)
                    local = self.settings.media_index_root / path.removeprefix('/data/litepan-strm/')
                    if not local.is_file():
                        updates.append({'Path': path, 'UpdateType': 'Deleted'})
            start += len(items)
            if not items or start >= payload.get('TotalRecordCount', start):
                break
        # Also recover replacements generated before this workflow's snapshot.
        for local in root.rglob('*.strm'):
            if local.is_file() and not local.is_symlink():
                path = '/data/litepan-strm/' + str(local.relative_to(self.settings.media_index_root))
                if path not in indexed:
                    updates.append({'Path': path, 'UpdateType': 'Created'})
        return updates

    def run(self, paths):
        self.login()
        tasks = self.match_tasks(paths, self.api('/admin/strm/tasks'))
        self.report(f'已合并为 {len(tasks)} 个 LitePan 目录任务，逐个同步')
        pending_file = self.settings.work_root / 'litepan-emby-pending.json'
        updates = json.loads(pending_file.read_text()) if pending_file.is_file() else []
        changed = self.notify_pending(updates, pending_file)
        completed = failed = 0
        errors = []
        for index, task in enumerate(tasks, 1):
            task = self.wait_task(task['id'])
            self.invalidate_directory_paths(paths, task)
            root = self.settings.media_index_root / task.get('group_dir', '') / task['output_folder']
            before = self.snapshot(root)
            previous = task.get('last_scan')
            self.report(f'[{index}/{len(tasks)}] LitePan 正在增量生成 STRM：{task["name"]}')
            self.api(f'/admin/strm/tasks/{task["id"]}/run?mode=auto', {})
            scan_error = None
            try:
                result = self.wait_task(task['id'], previous, allow_protected=True)
            except ValueError as exc:
                scan_error = str(exc)
                result = {'last_scan_status': 'failed', 'error_message': scan_error}
            # Native protected scans may already have written replacement files.
            self.quarantine_duplicates(root, task)
            after = self.snapshot(root)
            updates = []
            for relative in sorted(before.keys() | after.keys()):
                if before.get(relative) != after.get(relative):
                    updates.append({'Path': '/data/litepan-strm/' + relative,
                        'UpdateType': 'Deleted' if relative not in after else ('Created' if relative not in before else 'Modified')})
            changed_parents = {str(PurePosixPath(u['Path']).parent) for u in updates if u['UpdateType'] != 'Deleted'}
            updates.extend({'Path': p, 'UpdateType': 'Modified'} for p in sorted(changed_parents))
            self.save_json(pending_file, updates)
            changed += self.notify_pending(updates, pending_file)
            self.progress(completed=completed, failed=failed, current_index=index, total=len(tasks), changed_files=changed)
            self.report(f'已先提交 {len(updates)} 项确定的文件变化，再核对 Emby 索引')
            updates = []
            updates.extend(self.orphan_updates(root))
            # Refresh only affected show folders, not an entire media library.
            parents = {str(PurePosixPath(u['Path']).parent) for u in updates if u['UpdateType'] != 'Deleted'}
            updates.extend({'Path': p, 'UpdateType': 'Modified'} for p in sorted(parents))
            changed += self.notify_pending(updates, pending_file)
            if result.get('last_scan_status') != 'ok' and ('last_scan_status' in result or scan_error):
                failed += 1
                errors.append(task['name'] + '：' + (result.get('error_message') or '清理保护，已提交实际变更，未强制删除'))
            else:
                completed += 1
            self.progress(completed=completed, failed=failed, current_index=index, total=len(tasks), changed_files=changed)
            self.report(f'[{index}/{len(tasks)}] 已立即提交本目录 {len(updates)} 项 Emby 更新；刮削结果仍待 Emby 确认')
        if errors:
            raise ValueError('；'.join(errors))
        self.report(f'完成：Emby 已接受 {changed} 项定向更新通知；不等于刮削完成，无全库扫描')
        return changed
