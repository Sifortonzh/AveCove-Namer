import json
import sqlite3
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from avecove_namer.workflow import LitePanWorkflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        roots = base / 'roots.json'
        roots.write_text(json.dumps({'115': '/长夜留灯'}))
        dbpath = base / 'litepan.db'
        with sqlite3.connect(dbpath) as db:
            db.execute('CREATE TABLE strm_remote_dir_cache(account_id INTEGER,dir_id TEXT,dir_path TEXT)')
        self.settings = SimpleNamespace(litepan_roots_file=roots, media_index_root=base / 'strm',
            work_root=base, emby_url='http://emby', litepan_url='http://litepan', litepan_db_path=dbpath)
        self.flow = LitePanWorkflow(self.settings, lambda message: None, lambda: 'test-token')
        self.task = dict(id=1, account_id=1, status='active', path='/长夜留灯/00剧/01美',
            name='115-01美', group_dir='欧美', output_folder='115-01美', last_scan='old')

    def test_paths_in_same_task_are_merged(self):
        tasks = self.flow.match_tasks(['/115/00剧/01美/A', '/115/00剧/01美/B'], [self.task])
        self.assertEqual(len(tasks), 1)

    def test_unknown_task_rejected(self):
        with self.assertRaises(ValueError):
            self.flow.match_tasks(['/115/00影/01外/A'], [self.task])

    def test_path_cache_invalidation_is_scoped(self):
        with sqlite3.connect(self.settings.litepan_db_path) as db:
            db.executemany('INSERT INTO strm_remote_dir_cache VALUES (?,?,?)', [
                (1, 'old', '长夜留灯/00剧/01美/A/Old Season'),
                (1, 'other', '长夜留灯/00剧/01美/B/Season 01'),
                (2, 'other-provider', '长夜留灯/00剧/01美/A/Old Season')])
        self.flow.invalidate_directory_paths(['/115/00剧/01美/A'], self.task)
        with sqlite3.connect(self.settings.litepan_db_path) as db:
            self.assertEqual({row[0] for row in db.execute('SELECT dir_id FROM strm_remote_dir_cache')}, {'other', 'other-provider'})

    def test_snapshot_rejects_outside_root(self):
        with self.assertRaises(ValueError):
            self.flow.snapshot(Path('/etc'))

    def test_success_notifies_emby_and_clears_pending(self):
        root = self.settings.media_index_root / '欧美' / '115-01美'
        root.mkdir(parents=True)
        def api(path, body=None):
            if path == '/admin/strm/tasks':
                return [self.task]
            (root / 'A.strm').write_text('https://litepan/video')
        response = MagicMock()
        response.__enter__.return_value.status = 204
        with patch.object(self.flow, 'login'), patch.object(self.flow, 'api', side_effect=api), \
             patch.object(self.flow, 'orphan_updates', return_value=[]), \
             patch.object(self.flow, 'wait_task', return_value=self.task), \
             patch('urllib.request.urlopen', return_value=response) as post:
            self.assertEqual(self.flow.run(['/115/00剧/01美/A']), 2)
        body = json.loads(post.call_args.args[0].data)
        self.assertEqual(body['Updates'][0]['UpdateType'], 'Created')
        self.assertEqual(json.loads((self.settings.work_root / 'litepan-emby-pending.json').read_text()), [])

    def test_emby_failure_keeps_notifications(self):
        root = self.settings.media_index_root / '欧美' / '115-01美'
        root.mkdir(parents=True)
        def api(path, body=None):
            if path == '/admin/strm/tasks':
                return [self.task]
            (root / 'A.strm').write_text('https://litepan/video')
        with patch.object(self.flow, 'login'), patch.object(self.flow, 'api', side_effect=api), \
             patch.object(self.flow, 'orphan_updates', return_value=[]), \
             patch.object(self.flow, 'wait_task', return_value=self.task), \
             patch('urllib.request.urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                self.flow.run(['/115/00剧/01美/A'])
        self.assertEqual(len(json.loads((self.settings.work_root / 'litepan-emby-pending.json').read_text())), 2)

    def test_identity_ignores_token_but_preserves_quality_file_ids(self):
        root = self.settings.media_index_root / '欧美' / '115-01美'
        root.mkdir(parents=True)
        a, b, quality = [root / name for name in ('old.strm', 'new.strm', '4k.strm')]
        a.write_text('https://lite/api/strm/play/1/ZmlsZQ/t/old/n/a.mkv')
        b.write_text('https://lite/api/strm/play/1/ZmlsZQ/t/new/n/a.mkv')
        quality.write_text('https://lite/api/strm/play/1/b3RoZXI/t/new/n/4k.mkv')
        import os
        os.utime(a, ns=(1, 1))
        with patch.object(self.flow, 'api', return_value={'id': 'file', 'is_dir': False}):
            result = self.flow.quarantine_duplicates(root)
        self.assertEqual(len(result), 1)
        self.assertFalse(a.exists())
        self.assertTrue(b.exists())
        self.assertTrue(quality.exists())
        self.assertTrue(list((self.settings.work_root / 'strm-quarantine').rglob('manifest.json')))

    def test_ambiguous_same_timestamp_is_not_removed(self):
        root = self.settings.media_index_root / '欧美' / '115-01美'
        root.mkdir(parents=True)
        import os
        for name in ('a.strm', 'b.strm'):
            file = root / name
            file.write_text('https://lite/api/strm/play/1/ZmlsZQ/t/token/n/a.mkv')
            os.utime(file, ns=(1, 1))
        self.assertEqual(self.flow.quarantine_duplicates(root), [])

    def test_reconcile_detects_both_missing_new_index_and_old_orphan(self):
        root = self.settings.media_index_root / '欧美' / '115-01美'
        root.mkdir(parents=True)
        (root / 'new.strm').write_text('https://lite/video')
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [json.dumps([
            {'ItemId': '3', 'Locations': ['/data/litepan-strm/欧美']}]).encode(),
            json.dumps({'TotalRecordCount': 1, 'Items': [
            {'Path': '/data/litepan-strm/欧美/115-01美/old.strm'}]}).encode()]
        with patch('urllib.request.urlopen', return_value=response):
            updates = self.flow.orphan_updates(root)
        self.assertEqual({u['UpdateType'] for u in updates}, {'Created', 'Deleted'})

    def test_protected_task_does_not_block_next_task_notifications(self):
        second = dict(self.task, id=2, path='/长夜留灯/00剧/01英', output_folder='115-01英', name='115-01英')
        for task in (self.task, second):
            (self.settings.media_index_root / '欧美' / task['output_folder']).mkdir(parents=True)
        def api(path, body=None):
            if path == '/admin/strm/tasks':
                return [self.task, second]
        def wait(task_id, previous=None, **kwargs):
            task = self.task if task_id == 1 else second
            return dict(task, last_scan_status='protected' if task_id == 1 and previous else 'ok')
        with patch.object(self.flow, 'login'), patch.object(self.flow, 'api', side_effect=api), \
             patch.object(self.flow, 'wait_task', side_effect=wait), \
             patch.object(self.flow, 'orphan_updates', return_value=[{'Path': '/data/litepan-strm/old.strm', 'UpdateType': 'Deleted'}]), \
             patch.object(self.flow, 'notify_pending', return_value=1) as notify:
            with self.assertRaises(ValueError):
                self.flow.run(['/115/00剧/01美/A', '/115/00剧/01英/B'])
        self.assertEqual(notify.call_count, 5)
