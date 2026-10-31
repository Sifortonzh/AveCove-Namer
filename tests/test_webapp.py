import tempfile
import unittest
import os
from pathlib import Path

from avecove_namer.webapp import App, Settings, media_name_matches, normalized_lookup_query, normalized_media_name, recommended_folder_name, safe_cloud_path


class WebAppTests(unittest.TestCase):
    def test_shelf_artwork_prefers_source_language_then_textless(self):
        from unittest.mock import Mock
        app = App.__new__(App)
        client = Mock()
        app.tmdb = lambda: client
        client._get.side_effect = [
            {'original_language':'ko','original_name':'원제','name':'译名','poster_path':'/translated.jpg'},
            {'posters':[
                {'file_path':'/translated.jpg','iso_639_1':'zh','vote_count':100,'vote_average':10},
                {'file_path':'/source.jpg','iso_639_1':'ko','vote_count':1,'vote_average':7}],
             'backdrops':[{'file_path':'/plain.jpg','iso_639_1':None}]}
        ]
        row = app.shelf_metadata(42)
        self.assertEqual(row['posterPath'], '/source.jpg')
        self.assertEqual(row['backdropPath'], '/plain.jpg')
        self.assertEqual(row['originalTitle'], '원제')

    def test_single_episode_input_validation(self):
        from unittest.mock import Mock
        app = App.__new__(App)
        client = Mock()
        client.episode_details.return_value = {"code": "S01E02"}
        app.tmdb = lambda: client
        for code in ["S1E2", " s01 e02 ", "S001E002"]:
            self.assertEqual(app.episode_details({"tmdb_id": "1399", "code": code})["code"], "S01E02")
            client.episode_details.assert_called_with(1399, 1, 2)
        app.episode_details({"tmdb_id": "1399", "code": "S00E01"})
        client.episode_details.assert_called_with(1399, 0, 1)
        for code in ["S_E_", "S1E0", "../1", "S1E2E3", "S-1E1"]:
            with self.assertRaises(ValueError):
                app.episode_details({"tmdb_id": "1399", "code": code})

    def test_emby_page_removed_and_episode_dialog_available(self):
        web = Path(__file__).parents[1] / "src/avecove_namer/web"
        html = (web / "index.html").read_text()
        self.assertNotIn('data-view="emby"', html)
        self.assertNotIn('id="view-emby"', html)
        self.assertIn('id="episode-dialog"', html)
        self.assertIn('id="episode-season" inputmode="numeric"', html)
        self.assertIn('id="episode-number" inputmode="numeric"', html)

    def test_rename_completion_never_starts_emby_refresh(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        with tempfile.TemporaryDirectory() as directory:
            app = App.__new__(App)
            app.settings = SimpleNamespace(work_root=Path(directory))
            app._jobs = {"single": {}, "batch": {"completed_operations": 0}}
            app._jobs_lock = threading.Lock()
            backend = Mock()
            backend.scan.return_value = []
            app.openlist = lambda: backend
            app._remember_namer_completion = Mock()
            app.start_refresh_queue = Mock(side_effect=AssertionError("Emby must not be contacted"))
            operation = SimpleNamespace(kind="rename_file", source="/115/old", target="/115/new")
            plan = SimpleNamespace(root="/115/00剧/01美/Test", operations=[operation], policy={})
            with patch("avecove_namer.webapp.execute_plan", return_value=[operation]):
                app._run_apply("single", "plan", plan)
                app._run_batch_apply("batch", [("plan", plan)])
            app.start_refresh_queue.assert_not_called()
            for key in ["single", "batch"]:
                self.assertEqual(app._jobs[key]["status"], "completed")
                self.assertIsNone(app._jobs[key]["refresh_job"])

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

    def test_copy_folder_preserves_original_traditional_chinese(self):
        self.assertEqual(recommended_folder_name({'id':843,'title':'花样年华','original_title':'花樣年華','year':2000,'language':'cn'}), '花樣年華（2000） {tmdb=843}')

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

    def test_emby_pending_expands_movie_group_without_listing_the_container(self):
        class Backend:
            def list_directories(self, root, refresh=False):
                if root == "/115/00影/01国":
                    return [{"name": "总其他"}]
                if root == "/115/00影/01国/总其他":
                    return [{"name": "苹果 (2007)"}, {"name": "手机（2003）"}]
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
            paths = {item["path"] for item in result["pending"]}
            self.assertIn("/115/00影/01国/总其他/苹果 (2007)", paths)
            self.assertIn("/115/00影/01国/总其他/手机（2003）", paths)
            self.assertNotIn("/115/00影/01国/总其他", paths)
            self.assertTrue(all(item["kind"] == "movie" for item in result["pending"]))

    def test_emby_pending_does_not_assume_update_from_modified_time(self):
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
            app._checked_strm_difference = lambda *args: {'missing_files': 0, 'stale_files': 0}
            result = app.emby_pending()
            self.assertEqual(result["pending_count"], 0)
            app._checked_strm_difference = lambda *args: {'missing_files': 1, 'stale_files': 0}
            result = app.emby_pending()
            self.assertEqual(result["update_count"], 1)
            self.assertEqual(result['pending'][0]['missing_files'], 1)

    def test_strm_difference_counts_actual_files_not_folder_dates(self):
        from avecove_namer.models import Entry
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            title = root / 'local'
            title.mkdir()
            (title / 'Episode.S01E01.strm').write_text('https://lite/api/strm/play/1/id/n/Episode.S01E01.mkv')
            settings = Settings(host='127.0.0.1', port=8787, openlist_url='http://open',
                openlist_token_file=root/'token', tmdb_token_file=root/'tmdb',
                work_root=root/'jobs', detective_summary=root/'state', refresh_worker=root/'refresh')
            app = App(settings)
            class Backend:
                def scan(self, path, refresh=False):
                    return [Entry(path=path+'/Episode.S01E01.mkv'), Entry(path=path+'/Episode.S01E02.mkv')]
            app.openlist = lambda: Backend()
            delta = app._strm_file_difference('/115/Show', [title])
            self.assertEqual(delta['missing_files'], 1)
            self.assertEqual(delta['stale_files'], 0)
            (title / 'Episode.S01E03.strm').write_text('https://lite/api/strm/play/1/id/n/Episode.S01E03.mkv')
            self.assertEqual(app._strm_file_difference('/115/Show', [title])['stale_files'], 1)
            (title / 'Episode.S01E03.strm').unlink()
            (title / 'Episode.S01E02.strm').write_text('https://lite/api/strm/play/1/id/n/Episode.S01E02.mkv')
            season = title / 'Old Season Folder'
            season.mkdir()
            for file in list(title.glob('*.strm')):
                file.rename(season / file.name)
            delta = app._strm_file_difference('/115/Show', [title])
            self.assertEqual(delta['missing_files'], 0)
            self.assertEqual(delta['relocated_files'], 2)

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
            fresh = {"path": "/115/00剧/01美/Fresh raw 2026", "name": "Fresh raw 2026", "kind": "tv", "provider": "115", "category": "01美", "reason": "new_title", "modified": "2026-09-25T01:00:00Z"}
            container = {"path": "/115/00影/01国/总其他", "name": "总其他", "kind": "movie", "provider": "115", "category": "01国", "reason": "new_title", "modified": "2026-09-20T01:00:00Z"}
            app.emby_pending = lambda: {"mode": "shallow", "pending_count": 3, "pending": [done, fresh, container], "errors": []}
            app.openlist = lambda: type("EmptyBackend", (), {"list_directories": lambda self, path, refresh=False: []})()
            app._namer_completed_fingerprints = lambda: {done["path"]: "known"}
            app._known_namer_candidate_needs_work = lambda item, expected: False
            result = app.namer_pending()
            self.assertEqual(result["pending_count"], 1)
            self.assertEqual(result["pending"][0]["path"], fresh["path"])
            self.assertEqual(result["filtered_completed"], 2)

    def test_namer_pending_sees_grouped_movie_even_when_already_in_emby(self):
        class Backend:
            def list_directories(self, root, refresh=False):
                if root == "/115/00影/01国":
                    return [{"name": "总其他"}]
                if root == "/115/00影/01国/总其他":
                    return [{"name": "苹果 (2007)"}]
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
            app.emby_pending = lambda: {"mode": "shallow", "pending_count": 0, "pending": [], "errors": []}
            app._namer_completed_fingerprints = lambda: {}
            result = app.namer_pending()
            self.assertEqual(result["pending_count"], 1)
            self.assertEqual(result["pending"][0]["path"], "/115/00影/01国/总其他/苹果 (2007)")

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
