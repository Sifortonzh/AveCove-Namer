import unittest

from avecove_namer.detective import choose_tmdb_match, infer_search_terms, limit_plan_for_batch, parse_watch, season_numbers
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


if __name__ == "__main__":
    unittest.main()
