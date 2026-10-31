import json
import tempfile
import unittest
from pathlib import Path
from avecove_namer.webapp import App, Settings
from avecove_namer.models import Entry
from unittest.mock import Mock
import base64


class LitePanIndexTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.app = App(Settings(host='127.0.0.1', port=8787, openlist_url='http://unused',
            openlist_token_file=self.root/'token', tmdb_token_file=self.root/'tmdb',
            work_root=self.root/'jobs', detective_summary=self.root/'state', refresh_worker=self.root/'worker',
            media_index_root=self.root/'strm', backend_name='litepan'))

    def create(self, name, file_id='same', account='1'):
        path = self.app.settings.media_index_root / '01欧美剧集' / '115-01美' / 'Show (2020) {tmdb=42}' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'https://lite/api/strm/play/{account}/{file_id}/t/test/n/video.mkv')
        return path

    def test_same_episode_different_quality_id_is_not_duplicate(self):
        self.create('Season 01/Show.2020.S01E01.1080p.strm', 'hd')
        self.create('Season 01/Show.2020.S01E01.2160p.strm', 'uhd')
        self.assertEqual(self.app.emby_duplicates()['group_count'], 0)

    def test_pending_compares_file_ids_not_old_url_names_or_season_paths(self):
        encoded = base64.urlsafe_b64encode(b'12345').decode().rstrip('=')
        local = self.create('Season 1/New Name.strm', encoded)
        backend = Mock()
        backend.scan.return_value = [Entry('/115/Show/Season 01/Renamed.mkv', file_id='12345', account_id='1')]
        self.app.openlist = lambda: backend
        result = self.app._strm_file_difference('/115/Show', [local.parent.parent])
        self.assertEqual(result['missing_files'], 0)
        self.assertEqual(result['stale_files'], 0)
        self.assertEqual(result['relocated_files'], 0)
        backend.scan.return_value.append(Entry('/115/Show/Season 01/4k.mkv', file_id='54321', account_id='1'))
        self.assertEqual(self.app._strm_file_difference('/115/Show', [local.parent.parent])['missing_files'], 1)

    def test_renamed_folder_same_cloud_id_is_duplicate(self):
        self.create('Old Folder/Anything.strm')
        self.create('Season 01/Different Name.strm')
        result = self.app.emby_duplicates()
        self.assertEqual(result['remove_count'], 1)
        self.assertEqual(result['groups'][0]['provider'], '115')
        self.assertEqual(result['groups'][0]['series'], 'Show (2020) {tmdb=42}')

    def test_residual_recognizes_native_paths_and_labels_replacement(self):
        self.create('Season 01/Show.2020.S01E01.strm')
        self.app._emby_get = lambda *args: {'Items': [{'Id': 'old', 'Name': 'Show', 'Type': 'Series',
            'ProviderIds': {'Tmdb': '42'}, 'Path': '/data/litepan-strm/01欧美剧集/115-01美/Old Show'}]}
        result = self.app.emby_residuals()
        self.assertEqual(result['count'], 1)
        self.assertIn('新目录', result['items'][0]['reason'])
        self.assertEqual(result['items'][0]['provider'], '115')

    def test_unknown_legacy_links_are_not_auto_deleted_by_names(self):
        for name in ('Old/Show.2020.S01E01.strm', 'New/Show.2020.S01E01.strm'):
            self.create(name).write_text('https://old-provider/video')
        self.assertEqual(self.app.emby_duplicates()['group_count'], 0)

    def test_old_quality_collapsing_plan_is_rejected(self):
        keep = self.create('Season 01/a.strm', 'hd')
        remove = self.create('Season 01/b.strm', 'uhd')
        plan_id = 'a' * 16
        directory = self.app.settings.work_root/'duplicate-plans'
        directory.mkdir(parents=True)
        (directory/f'{plan_id}.json').write_text(json.dumps({'groups': [{'id': 'a'*12,
            'keep': str(keep.relative_to(self.app.settings.media_index_root)),
            'remove': [str(remove.relative_to(self.app.settings.media_index_root))]}]}))
        with self.assertRaises(ValueError):
            self.app.clean_emby_duplicates({'plan_id': plan_id, 'group_ids': 'all', 'confirmation': '清理 1 项'})
        self.assertTrue(remove.exists())
