import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from avecove_namer.webapp import App, Settings
from avecove_namer.models import Entry, RenamePlan, RenameOperation
from avecove_namer.backends import LocalBackend
from avecove_namer.executor import execute_plan, rollback


class CollectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.app = App(Settings(host='127.0.0.1', port=8787, openlist_url='http://unused',
            openlist_token_file=self.base/'token', tmdb_token_file=self.base/'tmdb',
            work_root=self.base/'jobs', detective_summary=self.base/'state', refresh_worker=self.base/'worker'))
        self.root = '/115/00影/01国/僵尸合集'
        self.backend = Mock(name='backend')
        self.backend.name = 'litepan'
        self.backend.scan.return_value = [Entry(self.root+'/Mr Vampire (1985) {tmdb=67342}/Mr.Vampire.1985.1080p.mkv')]
        self.backend.exists.return_value = False
        self.client = Mock()
        self.client.collection.return_value = {'name': '灵幻功夫僵尸鬼片（系列）', 'parts': [
            {'id': 67342, 'original_title': '殭屍先生', 'release_date': '1985-11-07'},
            {'id': 67345, 'original_title': '殭屍叔叔', 'release_date': '1988-12-22'}]}
        self.client.resolve_title.return_value = {'original_title': '殭屍先生', 'english_title': 'Mr. Vampire',
            'original_language': 'cn', 'year': 1985}
        self.app.openlist = lambda: self.backend
        self.app.tmdb = lambda: self.client

    def test_members_use_movie_id_original_title_and_outer_root_is_preserved(self):
        result = self.app.create_plan({'path': self.root, 'kind': 'collection', 'tmdb_id': 155421})
        dirs = [op for op in result['operations'] if op['kind'] == 'rename_directory']
        self.assertEqual(len(dirs), 1)
        self.assertIn('殭屍先生（1985） {tmdb=67342}', dirs[0]['target'])
        self.assertNotIn('155421', dirs[0]['target'])
        self.assertFalse(any(op['source'] == self.root and op['kind'] == 'rename_directory' for op in result['operations']))
        missing = [op for op in result['operations'] if op['kind'] == 'create_directory']
        self.assertEqual(missing[0]['target'], self.root+'/殭屍叔叔（1988） - 待入库')
        self.backend.mkdir.assert_not_called()

    def test_collection_child_files_use_same_original_language_as_folder(self):
        result = self.app.create_plan({'path': self.root, 'kind': 'collection', 'tmdb_id': 155421})
        videos = [op for op in result['operations'] if op['kind'] == 'rename_video']
        self.assertTrue(videos)
        self.assertTrue(all('殭屍先生' in op['target'] for op in videos))
        self.assertFalse(any('Mr.Vampire' in op['target'] for op in videos))

    def test_single_movie_source_language_applies_to_folder_and_file(self):
        path = self.root + '/Mr Vampire (1985) {tmdb=67342}'
        result = self.app.create_plan({'path': path, 'kind': 'movie', 'tmdb_id': 67342, 'source_parent': True})
        videos = [op for op in result['operations'] if op['kind'] == 'rename_video']
        self.assertTrue(videos)
        self.assertTrue(all('殭屍先生' in op['target'] for op in videos))

    def test_existing_placeholder_is_not_recreated(self):
        self.backend.exists.side_effect = lambda path: '待入库' in path
        result = self.app.create_plan({'path': self.root, 'kind': 'collection', 'tmdb_id': 155421})
        self.assertFalse(any(op['kind'] == 'create_directory' for op in result['operations']))

    def test_fill_missing_can_be_disabled(self):
        result = self.app.create_plan({'path': self.root, 'kind': 'collection', 'tmdb_id': 155421, 'fill_missing': False})
        self.assertFalse(any(op['kind'] == 'create_directory' for op in result['operations']))

    def test_off_collection_movie_is_preserved(self):
        self.backend.scan.return_value = [Entry(self.root+'/Other (1999) {tmdb=999}/Other.1999.mkv')]
        result = self.app.create_plan({'path': self.root, 'kind': 'collection', 'tmdb_id': 155421, 'fill_missing': False})
        self.assertFalse(result['operations'])
        self.assertEqual(result['members'][0]['status'], 'unresolved')

    def test_movie_container_is_detected_without_using_collection_as_movie(self):
        self.backend.scan.return_value += [Entry(self.root+'/Vampire Uncle (1988) {tmdb=67345}/Uncle.1988.mkv')]
        result = self.app.identify_path({'path': self.root, 'kind': 'movie'})
        self.assertEqual(result['kind'], 'collection')
        self.client.collection.assert_not_called()

    def test_single_movie_plan_cannot_overwrite_multiple_movies_even_with_manual_id(self):
        self.backend.scan.return_value += [Entry(self.root+'/Vampire Uncle (1988) {tmdb=67345}/Uncle.1988.mkv')]
        with self.assertRaisesRegex(ValueError, '多部独立电影'):
            self.app.create_plan({'path': self.root, 'kind': 'movie', 'tmdb_id': 181870, 'source_parent': True})

    def test_placeholder_requires_confirmed_execution_and_rollback_preserves_uploads(self):
        root = self.base/'collection'
        root.mkdir()
        target = root/'Missing（1988） - 待入库'
        plan = RenamePlan(1, 'now', 'local', str(root), {}, [RenameOperation(str(root), str(target), 'create_directory', 'missing', 1)])
        journal = self.base/'journal'
        execute_plan(plan, LocalBackend(), str(journal))
        self.assertFalse(target.exists())
        execute_plan(plan, LocalBackend(), str(journal), True, str(root), 1)
        self.assertTrue(target.is_dir())
        (target/'user-upload.mkv').touch()
        rollback(LocalBackend(), str(journal), execute=True)
        self.assertTrue((target/'user-upload.mkv').exists())


if __name__ == '__main__':
    unittest.main()
