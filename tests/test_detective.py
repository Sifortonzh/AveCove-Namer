import unittest

from avecove_namer.detective import choose_tmdb_match, infer_search_terms, parse_watch


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


if __name__ == "__main__":
    unittest.main()
