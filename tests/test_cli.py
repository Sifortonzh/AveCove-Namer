import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from avecove_namer.cli import main


class CliIntegrationTests(unittest.TestCase):
    def test_plan_apply_and_rollback_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Six Feet Under (2001)" / "Season 01"
            root.mkdir(parents=True)
            video = root / "Six.Feet.Under.S01E01.Pilot.1080p.BluRay.x265.mkv"
            subtitle = root / "Six.Feet.Under.S01E01.chs.sup"
            video.write_bytes(b"video")
            subtitle.write_bytes(b"subtitle")
            plan_path = Path(temp) / "plan.json"
            journal_path = Path(temp) / "journal.jsonl"

            with redirect_stdout(StringIO()):
                self.assertEqual(
                    main(
                        [
                            "plan",
                            "--backend",
                            "local",
                            "--path",
                            str(root.parent),
                            "--output",
                            str(plan_path),
                        ]
                    ),
                    0,
                )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(len(plan["operations"]), 2)

            with redirect_stdout(StringIO()):
                self.assertEqual(
                    main(
                        [
                            "apply",
                            "--backend",
                            "local",
                            "--plan",
                            str(plan_path),
                            "--journal",
                            str(journal_path),
                            "--execute",
                            "--confirm-root",
                            str(root.parent.resolve()),
                            "--confirm-count",
                            "2",
                        ]
                    ),
                    0,
                )
            self.assertTrue((root / "Six.Feet.Under.2001.S01E01.1080p.BluRay.x265.mkv").exists())
            self.assertTrue((root / "Six.Feet.Under.2001.S01E01.1080p.BluRay.x265.zh-CN.sup").exists())

            with redirect_stdout(StringIO()):
                self.assertEqual(
                    main(
                        [
                            "rollback",
                            "--backend",
                            "local",
                            "--journal",
                            str(journal_path),
                            "--execute",
                        ]
                    ),
                    0,
                )
            self.assertTrue(video.exists())
            self.assertTrue(subtitle.exists())


if __name__ == "__main__":
    unittest.main()
