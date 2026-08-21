import tempfile
import unittest
from pathlib import Path

from avecove_namer.backends import LocalBackend
from avecove_namer.executor import ExecutionError, execute_plan, rollback
from avecove_namer.naming import NamingPolicy
from avecove_namer.planner import make_plan


class ExecutorTests(unittest.TestCase):
    def test_local_execute_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Modern Family (2009)" / "Season 01"
            root.mkdir(parents=True)
            video = root / "Modern.Family.S01E01.Pilot.1080p.WEB-DL.x265.mkv"
            subtitle = root / "Modern.Family.S01E01.chs.sup"
            video.write_bytes(b"video")
            subtitle.write_bytes(b"subtitle")

            backend = LocalBackend()
            plan = make_plan(backend.scan(str(root.parent)), str(root.parent), "local", NamingPolicy())
            journal = Path(temp) / "journal.jsonl"
            completed = execute_plan(
                plan,
                backend,
                str(journal),
                execute=True,
                confirm_root=str(root.parent),
                confirm_count=2,
            )
            self.assertEqual(len(completed), 2)
            self.assertFalse(video.exists())
            self.assertTrue((root / "Modern.Family.2009.S01E01.1080p.WEB-DL.x265.mkv").exists())
            self.assertTrue((root / "Modern.Family.2009.S01E01.1080p.WEB-DL.x265.zh-CN.sup").exists())

            rollback(backend, str(journal), execute=True)
            self.assertTrue(video.exists())
            self.assertTrue(subtitle.exists())

    def test_exact_confirmation_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = LocalBackend()
            plan = make_plan([], temp, "local", NamingPolicy())
            with self.assertRaises(ExecutionError):
                execute_plan(plan, backend, str(Path(temp) / "journal.jsonl"), execute=True, confirm_root="wrong", confirm_count=0)

    def test_existing_journal_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Show (2020)"
            root.mkdir()
            source = root / "Show.S01E01.mkv"
            source.touch()
            backend = LocalBackend()
            plan = make_plan(backend.scan(str(root)), str(root), "local", NamingPolicy())
            journal = Path(temp) / "journal.jsonl"
            journal.write_text('{"old": "run"}\n', encoding="utf-8")
            with self.assertRaises(ExecutionError):
                execute_plan(plan, backend, str(journal), execute=True, confirm_root=str(root), confirm_count=1)

    def test_media_and_root_folder_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Kill Bill 1"
            root.mkdir()
            original_video = root / "杀死比尔.2003.2160p.REMUX.DV.mkv"
            original_video.touch()
            backend = LocalBackend()
            plan = make_plan(
                backend.scan(str(root)),
                str(root),
                "local",
                NamingPolicy(),
                "Kill Bill: Vol. 1",
                2003,
                True,
                24,
                "en",
            )
            journal = Path(temp) / "folder-rollback.jsonl"
            execute_plan(plan, backend, str(journal), True, str(root), 2)
            target_root = Path(temp) / "Kill Bill Vol.1 (2003) {tmdb=24}"
            self.assertTrue((target_root / "Kill.Bill.Vol.1.2003.2160p.REMUX.DV.mkv").exists())

            rollback(backend, str(journal), execute=True)
            self.assertTrue(original_video.exists())


if __name__ == "__main__":
    unittest.main()
