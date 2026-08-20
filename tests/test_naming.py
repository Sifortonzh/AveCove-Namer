import unittest

from avecove_namer.naming import NamingPolicy, build_subtitle_name, build_video_name, parse_media_name


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

    def test_subtitle_matches_complete_video_stem(self):
        self.assertEqual(
            build_subtitle_name(
                "Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.mkv",
                "Modern.Family.S01E01.chs.sup",
                NamingPolicy(),
            ),
            "Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.zh-CN.sup",
        )


if __name__ == "__main__":
    unittest.main()

