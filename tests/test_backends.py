import unittest
from unittest.mock import patch

from avecove_namer.backends import BackendError, OpenListBackend


class RecordingOpenListBackend(OpenListBackend):
    def __init__(self):
        super().__init__("https://openlist.example.com", "test-token")
        self.recorded = None
        self.recorded_calls = []

    def _request(self, endpoint, payload):
        self.recorded = (endpoint, payload)
        self.recorded_calls.append((endpoint, payload))
        return {}


class OpenListBackendTests(unittest.TestCase):
    def test_rename_uses_full_source_path_and_new_name(self):
        backend = RecordingOpenListBackend()
        backend.rename(
            "/115/TV/Show/Show.S01E01.mkv",
            "/115/TV/Show/Show.2020.S01E01.mkv",
        )
        self.assertEqual(
            backend.recorded,
            (
                "/api/fs/rename",
                {
                    "path": "/115/TV/Show/Show.S01E01.mkv",
                    "name": "Show.2020.S01E01.mkv",
                    "overwrite": False,
                },
            ),
        )

    def test_rename_cannot_move_between_directories(self):
        backend = RecordingOpenListBackend()
        with self.assertRaises(BackendError):
            backend.rename("/115/a/file.mkv", "/115/b/file.mkv")

    def test_case_only_rename_uses_a_temporary_name(self):
        backend = RecordingOpenListBackend()
        backend.rename_interval = 0
        backend.rename(
            "/115/Movies/Movie.1080p.Remux.mkv",
            "/115/Movies/Movie.1080p.REMUX.mkv",
        )
        self.assertEqual(len(backend.recorded_calls), 2)
        first_payload = backend.recorded_calls[0][1]
        second_payload = backend.recorded_calls[1][1]
        self.assertEqual(first_payload["path"], "/115/Movies/Movie.1080p.Remux.mkv")
        self.assertRegex(first_payload["name"], r"^\.avecove-namer-[0-9a-f]{12}\.tmp$")
        self.assertEqual(second_payload["path"], f"/115/Movies/{first_payload['name']}")
        self.assertEqual(second_payload["name"], "Movie.1080p.REMUX.mkv")

    def test_rename_retries_transient_provider_error(self):
        backend = RecordingOpenListBackend()
        backend.rename_interval = 0
        attempts = 0

        def request(endpoint, payload):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise BackendError("OpenList API error: temporary provider failure")
            backend.recorded = (endpoint, payload)
            return {}

        with patch.object(backend, "_request", side_effect=request):
            backend.rename("/115/TV/Show/Show.S01E01.mkv", "/115/TV/Show/Show.2020.S01E01.mkv")

        self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
