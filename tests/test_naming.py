import unittest

from avecove_namer.naming import (
    NamingPolicy,
    build_root_folder_name,
    build_subtitle_name,
    build_video_name,
    infer_context,
    parse_media_name,
)


class NamingTests(unittest.TestCase):
    def test_episode_year_is_added_and_episode_title_is_dropped(self):
        parsed = parse_media_name("Modern.Family.S01E01.Pilot.1080p.BluRay.x265.DTS.mkv")
        self.assertEqual(parsed.kind, "episode")
        self.assertEqual((parsed.season, parsed.episode), (1, 1))
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "Modern Family", 2009),
            "Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.mkv",
        )

    def test_existing_series_year_is_kept(self):
        parsed = parse_media_name("This.Is.Us.2016.S02E03.1080p.WEB-DL.DDP5.1.mkv")
        self.assertEqual(parsed.year, 2016)
        self.assertEqual(
            build_video_name(parsed, NamingPolicy()),
            "This.Is.Us.2016.S02E03.1080p.WEB-DL.DDP5.1.mkv",
        )

    def test_movie_year_and_release_data_are_preserved(self):
        parsed = parse_media_name("The.Godfather.1972.2160p.UHD.BluRay.REMUX.DV.HDR.mkv")
        self.assertEqual(parsed.year, 1972)
        self.assertEqual(
            build_video_name(parsed, NamingPolicy()),
            "The.Godfather.1972.2160p.UHD.BluRay.REMUX.DV.HDR.mkv",
        )

    def test_english_title_can_lead_a_foreign_movie(self):
        parsed = parse_media_name("Source.2003.2160p.REMUX.DV.mkv")
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "Kill Bill", 2003),
            "Kill.Bill.2003.2160p.REMUX.DV.mkv",
        )

    def test_chinese_title_can_lead_a_chinese_series(self):
        parsed = parse_media_name("Source.S01E01.1080p.WEB-DL.mkv")
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "漫长的季节", 2023),
            "漫长的季节.2023.S01E01.1080p.WEB-DL.mkv",
        )

    def test_bilingual_title_is_supported(self):
        parsed = parse_media_name("Source.2003.2160p.REMUX.DV.mkv")
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "Kill Bill 杀死比尔", 2003),
            "Kill.Bill.杀死比尔.2003.2160p.REMUX.DV.mkv",
        )

    def test_english_folder_uses_spaced_ascii_parentheses(self):
        self.assertEqual(
            build_root_folder_name("Kill Bill: Vol. 1", 2003, 24, "en"),
            "Kill Bill Vol.1 (2003) {tmdb=24}",
        )

    def test_chinese_folder_uses_attached_full_width_parentheses(self):
        self.assertEqual(
            build_root_folder_name("漫长的季节", 2023, 205272, "zh"),
            "漫长的季节（2023） {tmdb=205272}",
        )

    def test_subtitle_matches_complete_video_stem(self):
        self.assertEqual(
            build_subtitle_name(
                "Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.mkv",
                "Modern.Family.S01E01.chs.sup",
                NamingPolicy(),
            ),
            "Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.zh-CN.sup",
        )

    def test_bilingual_subtitle_tag_is_preserved(self):
        self.assertEqual(
            build_subtitle_name(
                "Modern.Family.2009.S01E01.1080p.BluRay.x265.mkv",
                "Modern.Family.S01E01.chs&eng.ass",
                NamingPolicy(),
            ),
            "Modern.Family.2009.S01E01.1080p.BluRay.x265.chs&eng.ass",
        )

    def test_generated_tmdb_folder_can_be_read_back(self):
        self.assertEqual(
            infer_context(
                "/TV/Modern Family (2009) {tmdb=1421}/第一季/Modern.Family.S01E01.mkv"
            ),
            ("Modern Family", 2009),
        )
        self.assertEqual(
            infer_context(
                "/TV/漫长的季节（2023） {tmdb=205272}/Season01/漫长的季节.S01E01.mkv"
            ),
            ("漫长的季节", 2023),
        )


if __name__ == "__main__":
    unittest.main()
