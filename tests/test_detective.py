import unittest

from avecove_namer.detective import (
    choose_tmdb_match,
    discover_movie_works,
    infer_search_terms,
    limit_plan_for_batch,
    movie_search_candidates,
    movie_title_variants,
    movie_work_root,
    parse_watch,
    season_numbers,
)
from avecove_namer.models import Entry, RenameOperation, RenamePlan


class DetectiveTests(unittest.TestCase):
    def test_parse_watch(self):
        watch = parse_watch("tv:/GuangYa/00剧/01韩")
        self.assertEqual(watch.kind, "tv")
        self.assertEqual(watch.path, "/GuangYa/00剧/01韩")

    def test_infer_terms_from_release_folder(self):
        title, year = infer_search_terms("【现.在.不.是.出.轨.的.问.题 (2026)】1080.SDR.Friday")
        self.assertEqual(title, "现 在 不 是 出 轨 的 问 题")
        self.assertEqual(year, 2026)

    def test_infer_terms_from_canonical_folder(self):
        title, year = infer_search_terms("The Affair Was Just The Beginning (2026) {tmdb=301418}")
        self.assertEqual(title, "The Affair Was Just The Beginning")
        self.assertEqual(year, 2026)

    def test_infer_terms_strips_baidu_full_season_release_suffix(self):
        title, year = infer_search_terms("【大.小.谎.言 全2季】蓝光原盘REMUX 内封字幕")
        self.assertEqual(title, "大 小 谎 言")
        self.assertIsNone(year)

    def test_choose_unique_exact_match(self):
        match, score, reason = choose_tmdb_match(
            "现在不是出轨的问题",
            2026,
            [
                {
                    "id": 301418,
                    "title": "现在不是出轨的问题",
                    "original_title": "지금 불륜이 문제가 아닙니다",
                    "year": 2026,
                }
            ],
        )
        self.assertEqual(match["id"], 301418)
        self.assertEqual(score, 1.0)
        self.assertIn("high-confidence", reason)

    def test_reject_wrong_year(self):
        match, score, _ = choose_tmdb_match(
            "Example",
            2026,
            [{"id": 1, "title": "Example", "original_title": "Example", "year": 2025}],
        )
        self.assertIsNone(match)
        self.assertEqual(score, 0.0)

    def test_localized_duplicates_keep_best_title_for_same_tmdb_id(self):
        match, score, _ = choose_tmdb_match(
            "Chinese Zodiac",
            2012,
            [
                {"id": 98567, "title": "十二生肖", "original_title": "十二生肖", "year": 2012},
                {"id": 98567, "title": "Chinese Zodiac", "original_title": "十二生肖", "year": 2012},
            ],
        )
        self.assertEqual(match["id"], 98567)
        self.assertEqual(score, 1.0)

    def test_season_numbers_support_episode_and_disc_layouts(self):
        entries = [
            Entry("/TV/Show/Season 01/Show.S01E01.mkv"),
            Entry("/TV/Show/Season 02/Show.S02D01.iso"),
        ]
        self.assertEqual(season_numbers(entries), {1, 2})

    def test_season_numbers_support_chinese_season_folder(self):
        entries = [Entry("/TV/Show/第一季/01.mkv"), Entry("/TV/Show/第06季/01.mkv")]
        self.assertEqual(season_numbers(entries), {1, 6})

    def test_long_show_is_split_into_five_season_batches(self):
        operations = [
            RenameOperation(
                source=f"/TV/Show/Season {season:02d}/old.S{season:02d}E01.mkv",
                target=f"/TV/Show/Season {season:02d}/Show.2020.S{season:02d}E01.mkv",
                kind="rename_video",
                reason="test",
                confidence=1.0,
            )
            for season in range(1, 8)
        ]
        operations.append(
            RenameOperation(
                source="/TV/Show",
                target="/TV/Show (2020) {tmdb=1}",
                kind="rename_directory",
                reason="test",
                confidence=1.0,
            )
        )
        plan = RenamePlan(version=1, created_at="now", backend="openlist", root="/TV/Show", policy={}, operations=operations)
        batch, partial, seasons = limit_plan_for_batch(plan, max_seasons=5, max_operations=200)
        self.assertEqual(seasons, [1, 2, 3, 4, 5])
        self.assertTrue(partial)
        self.assertEqual(len(batch.operations), 6)

    def test_movie_collection_is_split_at_nested_release_folders(self):
        top = "/Movies/Fast Saga Collection"
        entries = [
            Entry(f"{top}/Fast 1/The.Fast.and.the.Furious.2001.2160p.REMUX.mkv"),
            Entry(f"{top}/Fast 2/2.Fast.2.Furious.2003.2160p.REMUX.mkv"),
        ]
        works = discover_movie_works("/Movies", top, entries)
        self.assertEqual([root for root, _ in works], [f"{top}/Fast 1", f"{top}/Fast 2"])

    def test_existing_nested_tmdb_folder_is_the_movie_root(self):
        path = "/Movies/Actor/Bluray/功夫（2004） {tmdb=9470}/功夫.2004.mkv"
        root = movie_work_root("/Movies", "/Movies/Actor", path)
        self.assertEqual(root, "/Movies/Actor/Bluray/功夫（2004） {tmdb=9470}")

    def test_bluray_stream_uses_folder_above_bdmv(self):
        path = "/Movies/Collection/Movie Release/BDMV/STREAM/00001.m2ts"
        root = movie_work_root("/Movies", "/Movies/Collection", path)
        self.assertEqual(root, "/Movies/Collection/Movie Release")

    def test_movie_filename_is_preferred_when_folder_is_noisy_and_has_no_year(self):
        root = "/Movies/速度与激情1 4K原盘REMUX 国英双音"
        candidates = movie_search_candidates(
            "速度与激情1 4K原盘REMUX 国英双音",
            [Entry(f"{root}/The.Fast.and.the.Furious.2001.2160p.REMUX.mkv")],
        )
        self.assertEqual(candidates[0], ("The Fast and the Furious", 2001))

    def test_bilingual_release_title_produces_english_and_chinese_variants(self):
        variants = movie_title_variants("十二生肖[60帧率版本][国语配音+中文字幕] Chinese Zodiac")
        self.assertEqual(variants, ["十二生肖 Chinese Zodiac", "Chinese Zodiac", "十二生肖"])

    def test_movie_title_variant_strips_year_parenthesis_fragment(self):
        self.assertEqual(movie_title_variants("猛鬼学堂 ("), ["猛鬼学堂"])

    def test_movie_release_folder_strips_technical_tail_for_search(self):
        candidates = movie_search_candidates(
            "速度与激情9 4K原盘REMUX 国英双音 杜比视界 特效字幕",
            [Entry("/Movies/F9.The.Fast.Saga.2021.2160p.REMUX.mkv")],
        )
        self.assertIn(("速度与激情9", None), candidates)


if __name__ == "__main__":
    unittest.main()
