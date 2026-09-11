import tempfile
import unittest
from pathlib import Path

from avecove_namer.webapp import Settings, safe_cloud_path


class WebAppTests(unittest.TestCase):
    def test_safe_cloud_path_accepts_supported_media_roots(self):
        self.assertEqual(safe_cloud_path("115/00剧/01美/Modern Family"), "/115/00剧/01美/Modern Family")
        self.assertEqual(safe_cloud_path("/GuangYa/00影/01外/Test"), "/GuangYa/00影/01外/Test")

    def test_safe_cloud_path_rejects_traversal_and_unknown_roots(self):
        with self.assertRaises(ValueError):
            safe_cloud_path("/115/../secrets")
        with self.assertRaises(ValueError):
            safe_cloud_path("/Other/Movies")

    def test_settings_support_local_test_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                host="127.0.0.1",
                port=8787,
                openlist_url="http://127.0.0.1:5245",
                openlist_token_file=Path(directory) / "openlist.token",
                tmdb_token_file=Path(directory) / "tmdb.token",
                work_root=Path(directory) / "jobs",
                detective_summary=Path(directory) / "summary.json",
                refresh_worker=Path(directory) / "refresh.py",
            )
            self.assertEqual(settings.port, 8787)
