import tempfile
import unittest
import os
from pathlib import Path

from avecove_namer.webapp import App, Settings, media_name_matches, normalized_lookup_query, normalized_media_name, recommended_folder_name, safe_cloud_path


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

    def test_lookup_query_accepts_dotted_release_names(self):
        self.assertEqual(
            normalized_lookup_query("Teenage.Sex.And.Death.At.Camp.Miasma."),
            ("Teenage Sex And Death At Camp Miasma", None),
        )
        self.assertEqual(
            normalized_lookup_query("假面女郎 2023 WEB 4K 杜比视界"),
            ("假面女郎", 2023),
        )

    def test_detective_returns_latest_twenty_manual_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                host="127.0.0.1",
                port=8787,
                openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token",
                tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs",
                detective_summary=root / "detective" / "last-summary.json",
                refresh_worker=root / "refresh.py",
                manual_history=root / "manual-history.jsonl",
            )
            app = App(settings)
            for index in range(23):
                app._append_manual_event({"path": f"/115/00剧/01美/Test {index}", "status": "identified"})
            events = app.detective()["manual_events"]
            self.assertEqual(len(events), 20)
            self.assertTrue(events[0]["path"].endswith("Test 22"))
            self.assertTrue(events[-1]["path"].endswith("Test 3"))

    def test_emby_pending_only_lists_cloud_titles_without_local_strm(self):
        class Backend:
            def list_directories(self, root, refresh=False):
                if root == "/115/00剧/01美":
                    return [{"name": "Existing (2020)"}, {"name": "New Show (2026)"}]
                return []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "media" / "115/00剧/01美/Existing (2020)"
            local.mkdir(parents=True)
            (local / "episode.strm").write_text("https://example.invalid/video", encoding="utf-8")
            settings = Settings(
                host="127.0.0.1", port=8787, openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token", tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs", detective_summary=root / "summary.json",
                refresh_worker=root / "refresh.py", media_index_root=root / "media",
            )
            app = App(settings)
            app.openlist = lambda: Backend()
            result = app.emby_pending()
            self.assertEqual(result["pending_count"], 1)
            self.assertEqual(result["pending"][0]["path"], "/115/00剧/01美/New Show (2026)")

    def test_emby_pending_matches_korean_and_english_folders_by_tmdb_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            class Backend:
                def list_directories(self, path, refresh=False):
                    if path == "/115/00剧/01韩":
                        return [{"name": "대장금 (2003) {tmdb=333}"}]
                    return []

            local = root / "media/115/00剧/01韩/Jewel in the Palace (2003) {tmdb=333}"
            local.mkdir(parents=True)
            (local / "Jewel.in.the.Palace.2003.S01E01.strm").write_text("https://example.invalid/video", encoding="utf-8")
            settings = Settings(
                host="127.0.0.1", port=8787, openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token", tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs", detective_summary=root / "summary.json",
                refresh_worker=root / "refresh.py", media_index_root=root / "media",
            )
            app = App(settings)
            app.openlist = lambda: Backend()
            result = app.emby_pending()
            self.assertEqual(result["pending_count"], 0)
            self.assertEqual(result["present_titles"], 1)

    def test_emby_pending_marks_movie_roots_for_movie_identification(self):
        class Backend:
            def list_directories(self, root, refresh=False):
                if root == "/115/00影/01外":
                    return [{"name": "Unsorted Movie 2026"}]
                return []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                host="127.0.0.1", port=8787, openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token", tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs", detective_summary=root / "summary.json",
                refresh_worker=root / "refresh.py", media_index_root=root / "media",
            )
            app = App(settings)
            app.openlist = lambda: Backend()
            result = app.emby_pending()
            movie = next(item for item in result["pending"] if item["path"].startswith("/115/00影/01外/"))
            self.assertEqual(movie["kind"], "movie")

    def test_emby_pending_detects_subscription_update_by_modified_time(self):
        class Backend:
            def list_directories(self, root, refresh=False):
                if root == "/115/00剧/01美":
                    return [{"name": "Existing (2020)", "modified": "2026-09-24T01:00:00Z"}]
                return []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "media/115/00剧/01美/Existing (2020)"
            local.mkdir(parents=True)
            episode = local / "Existing.2020.S01E01.strm"
            episode.write_text("https://example.invalid/video", encoding="utf-8")
            os.utime(episode, (1_700_000_000, 1_700_000_000))
            settings = Settings(
                host="127.0.0.1", port=8787, openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token", tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs", detective_summary=root / "summary.json",
                refresh_worker=root / "refresh.py", media_index_root=root / "media",
            )
            app = App(settings)
            app.openlist = lambda: Backend()
            result = app.emby_pending()
            self.assertEqual(result["pending_count"], 1)
            self.assertEqual(result["pending"][0]["reason"], "subscription_update")

    def test_namer_pending_hides_verified_completed_items_but_keeps_new_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                host="127.0.0.1", port=8787, openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token", tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs", detective_summary=root / "detective/last-summary.json",
                refresh_worker=root / "refresh.py", media_index_root=root / "media",
                namer_completed_state=root / "namer-completed.json",
            )
            app = App(settings)
            done = {"path": "/123/00影/01外/Done (2020) {tmdb=1}", "name": "Done (2020) {tmdb=1}", "kind": "movie", "provider": "123", "category": "01外", "reason": "subscription_update", "modified": "2026-09-24T01:00:00Z"}
            fresh = {"path": "/115/00剧/01美/Fresh (2026) {tmdb=2}", "name": "Fresh (2026) {tmdb=2}", "kind": "tv", "provider": "115", "category": "01美", "reason": "subscription_update", "modified": "2026-09-25T01:00:00Z"}
            container = {"path": "/115/00影/01国/总其他", "name": "总其他", "kind": "movie", "provider": "115", "category": "01国", "reason": "new_title", "modified": "2026-09-20T01:00:00Z"}
            app.emby_pending = lambda: {"mode": "shallow", "pending_count": 3, "pending": [done, fresh, container], "errors": []}
            app._namer_completed_fingerprints = lambda: {done["path"]: "known"}
            app._known_namer_candidate_needs_work = lambda item, expected: False
            result = app.namer_pending()
            self.assertEqual(result["pending_count"], 1)
            self.assertEqual(result["pending"][0]["path"], fresh["path"])
            self.assertEqual(result["filtered_completed"], 2)

    def test_emby_duplicate_plan_keeps_one_episode_and_only_previews_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            first = media / "123/00剧/01美/Show (2020)/Old/Show.2020.S01E01.1080p.strm"
            second = media / "123/00剧/01美/Show (2020)/Show.2020.S01E01.2160p.strm"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True, exist_ok=True)
            first.write_text("old", encoding="utf-8")
            second.write_text("new", encoding="utf-8")
            settings = Settings(
                host="127.0.0.1", port=8787, openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token", tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs", detective_summary=root / "summary.json",
                refresh_worker=root / "refresh.py", media_index_root=media,
            )
            result = App(settings).emby_duplicates()
            self.assertEqual(result["group_count"], 1)
            self.assertEqual(result["remove_count"], 1)
            self.assertEqual(result["groups"][0]["provider"], "123")
            self.assertEqual(result["groups"][0]["series"], "Show (2020)")
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())

    def test_emby_duplicate_plan_prefers_matching_season_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            correct = media / "115/00剧/01美/Show (2020)/Season 01/Show.2020.S01E02.strm"
            stale = media / "115/00剧/01美/Show (2020)/Season 04/Show.2020.S01E02.strm"
            correct.parent.mkdir(parents=True)
            stale.parent.mkdir(parents=True)
            correct.write_text("correct", encoding="utf-8")
            stale.write_text("stale", encoding="utf-8")
            settings = Settings(
                host="127.0.0.1", port=8787, openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token", tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs", detective_summary=root / "summary.json",
                refresh_worker=root / "refresh.py", media_index_root=media,
            )
            result = App(settings).emby_duplicates()
            self.assertIn("Season 01", result["groups"][0]["keep"])
            self.assertIn("Season 04", result["groups"][0]["remove"][0])

    def test_emby_duplicate_plan_groups_different_language_titles_by_tmdb_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            korean = media / "115/00剧/01韩/대장금 (2003) {tmdb=333}/Season 01/Dae.Jang.Geum.2003.S01E01.strm"
            english = media / "115/00剧/01韩/Jewel in the Palace (2003) {tmdb=333}/Season 01/Jewel.in.the.Palace.2003.S01E01.strm"
            korean.parent.mkdir(parents=True)
            english.parent.mkdir(parents=True)
            korean.write_text("same-url", encoding="utf-8")
            english.write_text("same-url", encoding="utf-8")
            settings = Settings(
                host="127.0.0.1", port=8787, openlist_url="http://127.0.0.1:5245",
                openlist_token_file=root / "openlist.token", tmdb_token_file=root / "tmdb.token",
                work_root=root / "jobs", detective_summary=root / "summary.json",
                refresh_worker=root / "refresh.py", media_index_root=media,
            )
            result = App(settings).emby_duplicates()
            self.assertEqual(result["group_count"], 1)
            self.assertEqual(result["remove_count"], 1)
