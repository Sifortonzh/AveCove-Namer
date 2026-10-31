import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from avecove_namer.webapp import App, Settings
from avecove_namer.models import RenameOperation, RenamePlan
from avecove_namer.planner import write_plan
from avecove_namer.workflow import LitePanWorkflow


class InlineReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = App(Settings(host='127.0.0.1', port=8787, openlist_url='http://unused',
            openlist_token_file=self.root/'token', tmdb_token_file=self.root/'tmdb',
            work_root=self.root/'jobs', detective_summary=self.root/'state', refresh_worker=self.root/'worker',
            media_index_root=self.root/'strm', backend_name='litepan'))
        self.item = {'id': '123', 'path': '/Movies/123-01国/Old/a.strm', 'name': 'Old',
                     'reason': '本地引用缺失', 'emby_path': '/data/litepan-strm/Movies/123-01国/Old/a.strm'}
        self.app.emby_residuals = lambda: {'items': [self.item]}
        self.payload = {'id': '123', 'path': self.item['path'], 'action': 'clean', 'confirmation': '清理旧索引 123'}

    def test_clean_notifies_missing_index_without_deleting_files(self):
        with patch.object(LitePanWorkflow, 'notify_pending', return_value=1) as notify:
            result = self.app.review_emby_residual(self.payload)
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(notify.call_args.args[0], [{'Path': self.item['emby_path'], 'UpdateType': 'Deleted'}])
        self.assertTrue(list((self.app.settings.work_root/'residual-reviews').glob('*.json')))

    def test_existing_path_or_changed_path_refuses_clean(self):
        file = self.app.settings.media_index_root/self.item['path'].lstrip('/')
        file.parent.mkdir(parents=True)
        file.touch()
        with self.assertRaisesRegex(ValueError, '仍存在'):
            self.app.review_emby_residual(self.payload)
        with self.assertRaisesRegex(ValueError, '路径已变化'):
            self.app.review_emby_residual({**self.payload, 'path':'/Other'})
        self.assertTrue(file.exists())

    def test_keep_is_persisted_and_verify_undoes_keep(self):
        self.app.review_emby_residual({**self.payload, 'action':'keep'})
        self.assertTrue(self.app._residual_decisions())
        self.app.review_emby_residual({**self.payload, 'action':'verify'})
        self.assertFalse(self.app._residual_decisions())

    def test_namer_review_skips_conflicts_and_keeps_original_plan(self):
        plan_id = 'a'*24
        directory = self.app.settings.work_root/plan_id
        directory.mkdir(parents=True)
        plan = RenamePlan(1, 'now', 'litepan', '/123/Film', {}, [
            RenameOperation('/123/Film/a.mkv', '/123/Film/new.mkv', 'rename_video', 'test', 1),
            RenameOperation('/123/Film/b.srt', '/123/Film/new.srt', 'rename_subtitle', 'test', 1)],
            conflicts=['Target exists: /123/Film/new.mkv'])
        write_plan(plan, str(directory/'plan.json'), str(directory/'plan.csv'))
        backend = Mock()
        backend.exists.side_effect = lambda path: path.endswith('.mkv')
        self.app.openlist = lambda: backend
        result = self.app.review_namer_plan({'plan_id':plan_id, 'skip_conflicts':True})
        self.assertFalse(result['conflicts'])
        self.assertEqual(len(result['operations']), 1)
        self.assertNotEqual(result['plan_id'], plan_id)
        backend.rename.assert_not_called()

    def test_unknown_conflict_is_not_bypassed(self):
        plan_id = 'b'*24
        directory = self.app.settings.work_root/plan_id
        directory.mkdir(parents=True)
        plan = RenamePlan(1, 'now', 'litepan', '/123/Film', {}, [], conflicts=['Unsafe source'])
        write_plan(plan, str(directory/'plan.json'), str(directory/'plan.csv'))
        self.app.openlist = lambda: Mock()
        result = self.app.review_namer_plan({'plan_id':plan_id, 'skip_conflicts':True})
        self.assertEqual(result['conflicts'], ['Unsafe source'])

    def test_bulk_clean_skips_kept_existing_and_changed_items(self):
        kept = {**self.item, 'id':'124', 'review':'keep'}
        existing = {**self.item, 'id':'125', 'path':'/Movies/123-01国/existing.strm'}
        file = self.app.settings.media_index_root/existing['path'].lstrip('/')
        file.parent.mkdir(parents=True)
        file.touch()
        self.app.emby_residuals = lambda: {'items':[self.item, kept, existing]}
        with patch.object(LitePanWorkflow, 'notify_pending', return_value=1) as notify:
            result = self.app.clean_emby_residuals({'ids':['123','124','125','126'], 'confirmation':'清理 4 项旧索引'})
        self.assertEqual(result['submitted_ids'], ['123'])
        self.assertEqual(len(result['skipped']), 3)
        self.assertEqual(len(notify.call_args.args[0]), 1)
        self.assertTrue(file.exists())

    def test_bulk_clean_requires_confirmation(self):
        with self.assertRaisesRegex(ValueError, '确认'):
            self.app.clean_emby_residuals({'ids':['123']})

    def test_directory_catalog_preserves_actual_levels(self):
        self.app._cloud_directories = lambda *args: [{'name':'01欧'}, {'name':'01中'}]
        self.app.openlist = lambda: Mock()
        result = self.app.library_directories('/115/00剧')
        self.assertEqual({item['path'] for item in result['directories']}, {'/115/00剧/01欧','/115/00剧/01中'})
