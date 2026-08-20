import unittest

from avecove_namer.backends import BackendError, OpenListBackend


class RecordingOpenListBackend(OpenListBackend):
    def __init__(self):
        super().__init__("https://openlist.example.com", "test-token")
        self.recorded = None

    def _request(self, endpoint, payload):
        self.recorded = (endpoint, payload)
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


if __name__ == "__main__":
    unittest.main()
