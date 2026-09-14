import tempfile
import unittest
from pathlib import Path

from avecove_namer.webapp import Settings, media_name_matches, normalized_media_name, recommended_folder_name, safe_cloud_path


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

    def test_recommended_folder_name_follows_origin_punctuation(self):
        self.assertEqual(
            recommended_folder_name({"id": 24, "title": "杀死比尔", "original_title": "Kill Bill: Vol. 1", "year": 2003, "language": "en"}),
            "Kill Bill: Vol. 1 (2003) {tmdb=24}",
        )
        self.assertEqual(
            recommended_folder_name({"id": 123, "title": "漫长的季节", "original_title": "漫长的季节", "year": 2023, "language": "zh"}),
            "漫长的季节（2023） {tmdb=123}",
        )

    def test_media_name_matching_ignores_year_tmdb_and_punctuation(self):
        aliases = {normalized_media_name("爱乐之城"), normalized_media_name("La La Land")}
        self.assertTrue(media_name_matches("La La Land (2016) {tmdb=313369}", aliases))
        self.assertTrue(media_name_matches("【爱乐之城】4K.HDR.REMUX", aliases))
        self.assertFalse(media_name_matches("Modern Family (2009)", aliases))
