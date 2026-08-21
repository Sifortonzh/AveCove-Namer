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


if __name__ == "__main__":
    unittest.main()
