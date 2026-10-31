import io
import unittest
from unittest.mock import patch

from avecove_namer.tmdb import TMDBClient


class TMDBClientTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_v3_api_key_is_sent_as_query_parameter(self, urlopen):
        urlopen.return_value = io.BytesIO(b'{"results": []}')
        client = TMDBClient("0123456789abcdef0123456789abcdef")
        self.assertEqual(client.search("Kill Bill", "movie", 2003), [])
        request = urlopen.call_args.args[0]
        self.assertIn("api_key=0123456789abcdef0123456789abcdef", request.full_url)
        self.assertIsNone(request.get_header("Authorization"))

    @patch("urllib.request.urlopen")
    def test_read_token_is_sent_as_bearer_header(self, urlopen):
        urlopen.return_value = io.BytesIO(b'{"results": []}')
        client = TMDBClient("header.payload.signature")
        self.assertEqual(client.search("Kill Bill", "movie", 2003), [])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer header.payload.signature")
        self.assertNotIn("api_key=", request.full_url)

    def test_auto_title_uses_english_for_non_chinese_origin(self):
        client = TMDBClient("0123456789abcdef0123456789abcdef")
        client.details = lambda tmdb_id, kind, language: {
            "title": "杀死比尔" if language == "zh-CN" else "Kill Bill: Vol. 1",
            "original_title": "Kill Bill: Vol. 1",
            "original_language": "en",
            "release_date": "2003-10-10",
        }
        resolved = client.resolve_title(24, "movie", "auto")
        self.assertEqual(resolved["title"], "Kill Bill: Vol. 1")
        self.assertEqual(resolved["primary_language"], "en")

    def test_auto_title_uses_chinese_for_chinese_origin(self):
        client = TMDBClient("0123456789abcdef0123456789abcdef")
        client.details = lambda tmdb_id, kind, language: {
            "name": "漫长的季节" if language == "zh-CN" else "The Long Season",
            "original_name": "漫长的季节",
            "original_language": "zh",
            "first_air_date": "2023-04-22",
        }
        resolved = client.resolve_title(205272, "tv", "auto")
        self.assertEqual(resolved["title"], "漫长的季节")
        self.assertEqual(resolved["primary_language"], "zh")

    def test_episode_details_fetches_only_requested_episode_in_source_and_chinese(self):
        client = TMDBClient("test-token")
        client.details = lambda *args: {"name": "权力的游戏", "original_name": "Game of Thrones", "original_language": "en"}
        calls = []
        def fake_get(endpoint, params):
            calls.append((endpoint, params))
            return {"name": "Winter Is Coming" if params["language"] == "en" else "凛冬将至", "overview": "简介", "vote_average": 8.5, "vote_count": 10}
        client._get = fake_get
        result = client.episode_details(1399, 1, 1)
        self.assertEqual(result["code"], "S01E01")
        self.assertEqual(result["name"], "Winter Is Coming")
        self.assertEqual(result["translated_name"], "凛冬将至")
        self.assertEqual([call[0] for call in calls], ["/tv/1399/season/1/episode/1"] * 2)
        self.assertEqual([call[1]["language"] for call in calls], ["en", "zh-CN"])

    def test_episode_details_allows_specials_and_rejects_invalid_numbers(self):
        client = TMDBClient("test-token")
        client.details = lambda *args: {"original_language": "zh-CN"}
        client._get = lambda *args: {}
        self.assertEqual(client.episode_details(1399, 0, 1)["code"], "S00E01")
        for args in [(0, 1, 1), (1399, -1, 1), (1399, 1, 0)]:
            with self.assertRaises(ValueError):
                client.episode_details(*args)

    def test_original_episode_metadata_uses_original_language_for_each_season(self):
        client = TMDBClient("0123456789abcdef0123456789abcdef")
        client.details = lambda tmdb_id, kind, language: {
            "original_name": "지금 불륜이 문제가 아닙니다",
            "original_language": "ko",
            "seasons": [{"season_number": 1}, {"season_number": 0}],
        }
        calls = []

        def fake_get(endpoint, params):
            calls.append((endpoint, params))
            number = int(endpoint.rsplit("/", 1)[-1])
            return {"episodes": [{"episode_number": 1, "name": f"제목 {number}", "overview": None}]}

        client._get = fake_get
        result = client.original_episode_metadata(301418)
        self.assertEqual(result["original_language"], "ko")
        self.assertEqual([item["season"] for item in result["episodes"]], [0, 1])
        self.assertEqual(result["episodes"][0]["overview"], "")
        self.assertEqual(calls, [
            ("/tv/301418/season/0", {"language": "ko"}),
            ("/tv/301418/season/1", {"language": "ko"}),
        ])

    def test_discover_keeps_media_kind_and_language_filter(self):
        client = TMDBClient("0123456789abcdef0123456789abcdef")
        calls = []

        def fake_get(endpoint, params):
            calls.append((endpoint, params))
            return {"results": [{"id": 7, "title": "测试电影", "original_title": "Test", "original_language": "zh", "release_date": "2026-01-02", "poster_path": "/x.jpg"}]}

        client._get = fake_get
        result = client.discover("movie", original_language="zh")
        self.assertEqual(result[0]["kind"], "movie")
        self.assertEqual(result[0]["year"], 2026)
        self.assertEqual(calls[0][1]["with_original_language"], "zh")

    def test_trailer_prefers_official_trailer_over_teaser(self):
        client = TMDBClient("0123456789abcdef0123456789abcdef")

        def fake_get(endpoint, params):
            if params["language"] == "zh-CN":
                return {"results": [{"key": "teaser", "site": "YouTube", "type": "Teaser", "official": True, "size": 1080}]}
            return {"results": [{"key": "trailer", "site": "YouTube", "type": "Trailer", "official": True, "size": 720}]}

        client._get = fake_get
        result = client.trailer(7, "movie")
        self.assertEqual(result["key"], "trailer")
        self.assertIn("youtube-nocookie.com/embed/trailer", result["embed_url"])


if __name__ == "__main__":
    unittest.main()
