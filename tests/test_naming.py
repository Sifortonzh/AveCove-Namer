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

    def test_episode_ep_variant_is_supported(self):
        parsed = parse_media_name("S01EP76.2011.2160p.WEB-DL.H265.mp4")
        self.assertEqual(parsed.kind, "episode")
        self.assertEqual((parsed.season, parsed.episode), (1, 76))
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "甄嬛传", 2011),
            "甄嬛传.2011.S01E76.2160p.WEB-DL.H265.mp4",
        )

    def test_multi_episode_iso_range_is_preserved(self):
        parsed = parse_media_name("疑犯追踪(2011).S01E01-E06.第06集.iso")
        self.assertEqual((parsed.season, parsed.episode, parsed.episode_end), (1, 1, 6))
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "Person of Interest", 2011),
            "Person.of.Interest.2011.S01E01-E06.iso",
        )

    def test_tv_bluray_iso_disc_is_preserved(self):
        parsed = parse_media_name("Camelot.2011.S01.D03.1080p.BluRay.AVC.TrueHD.5.1.iso")
        self.assertEqual((parsed.kind, parsed.season, parsed.disc), ("disc", 1, 3))
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "Camelot", 2011),
            "Camelot.2011.S01D03.1080p.BluRay.AVC.TrueHD.5.1.iso",
        )

    def test_season_word_episode_is_supported(self):
        parsed = parse_media_name("Yellowstone.Season3E01.2020.1080p.BluRay.mkv")
        self.assertEqual((parsed.kind, parsed.season, parsed.episode), ("episode", 3, 1))

    def test_bare_e_episode_is_supported_for_tv(self):
        parsed = parse_media_name(
            "法医秦明.Medical.Examiner.Dr.Qin.2016.E01.1080p.WEB-DL.mp4",
            allow_bare_episode=True,
        )
        self.assertEqual((parsed.kind, parsed.season, parsed.episode), ("episode", 1, 1))

    def test_trailing_episode_number_is_supported_for_tv(self):
        parsed = parse_media_name("温柔的背后 30.mp4", allow_bare_episode=True)
        self.assertEqual((parsed.kind, parsed.season, parsed.episode), ("episode", 1, 30))

    def test_language_variant_before_technical_tail_is_preserved(self):
        parsed = parse_media_name("越狱.S04E22.粤语.2008.BluRay.REMUX.1080p.mkv")
        self.assertEqual(parsed.technical_tail, ("粤语", "BluRay", "REMUX", "1080p"))

    def test_technical_tail_before_episode_is_preserved(self):
        parsed = parse_media_name(
            "The Big Bang Theory 1080p BluRay REMUX AVC DTS-HD MA S09E23 - 第 23 集.mkv"
        )
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "The Big Bang Theory", 2007),
            "The.Big.Bang.Theory.2007.S09E23.1080p.BluRay.REMUX.AVC.DTS-HD.MA.mkv",
        )

    def test_bare_numbered_tv_episode_is_supported_when_explicitly_enabled(self):
        parsed = parse_media_name(
            "161.SDR.8bit.2160p.60fps.DDP5.1.WEB-DL.H265.mp4",
            allow_bare_episode=True,
        )
        self.assertEqual((parsed.kind, parsed.season, parsed.episode), ("episode", 1, 161))
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "斗罗大陆II绝世唐门", 2023),
            "斗罗大陆II绝世唐门.2023.S01E161.SDR.8bit.2160p.60fps.DDP5.1.WEB-DL.H265.mp4",
        )

    def test_movie_year_and_release_data_are_preserved(self):
        parsed = parse_media_name("The.Godfather.1972.2160p.UHD.BluRay.REMUX.DV.HDR.mkv")
        self.assertEqual(parsed.year, 1972)
        self.assertEqual(
            build_video_name(parsed, NamingPolicy()),
            "The.Godfather.1972.2160p.UHD.BluRay.REMUX.DV.HDR.mkv",
        )

    def test_movie_dvd_format_data_is_preserved(self):
        parsed = parse_media_name("猛鬼学堂 (1988) NTSC DVD5.iso")
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "猛鬼学堂", 1988),
            "猛鬼学堂.1988.NTSC.DVD5.iso",
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

    def test_chinese_comma_is_preserved(self):
        parsed = parse_media_name("Genius.Girlfriend.S01E01.2160p.WEB-DL.mp4")
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "天才，女友", 2026),
            "天才，女友.2026.S01E01.2160p.WEB-DL.mp4",
        )
        self.assertEqual(
            build_root_folder_name("天才，女友", 2026, 289116, "zh"),
            "天才，女友（2026） {tmdb=289116}",
        )

    def test_duplicate_series_year_is_removed_from_technical_tail(self):
        parsed = parse_media_name("御廷谣.2026.S01E14.2160p.2026.WEB-DL.H265.AAC.mp4")
        self.assertEqual(
            build_video_name(parsed, NamingPolicy(), "御廷谣", 2026),
            "御廷谣.2026.S01E14.2160p.WEB-DL.H265.AAC.mp4",
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

    def test_english_folder_preserves_apostrophe(self):
        self.assertEqual(
            build_root_folder_name("Harry Potter and the Philosopher's Stone", 2001, 671, "en"),
            "Harry Potter and the Philosopher's Stone (2001) {tmdb=671}",
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

    def test_chinese_bilingual_subtitle_tag_is_normalized(self):
        self.assertEqual(
            build_subtitle_name(
                "Modern.Family.2009.S01E13.1080p.BluRay.x265.mkv",
                "Modern.Family.S01E13.简体&英文.ass",
                NamingPolicy(),
            ),
            "Modern.Family.2009.S01E13.1080p.BluRay.x265.chs&eng.ass",
        )

    def test_episode_title_en_is_not_mistaken_for_a_language_tag(self):
        self.assertEqual(
            build_subtitle_name(
                "Modern.Family.2009.S01E07.1080p.BluRay.x265.mkv",
                "Modern.Family.S01E07.En.Garde.1080p.BluRay.ass",
                NamingPolicy(),
            ),
            "Modern.Family.2009.S01E07.1080p.BluRay.x265.zh-CN.ass",
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
