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


if __name__ == "__main__":
    unittest.main()
