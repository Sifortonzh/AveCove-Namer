import unittest

from avecove_namer.models import Entry
from avecove_namer.naming import NamingPolicy
from avecove_namer.planner import make_plan


class PlannerTests(unittest.TestCase):
    def test_episode_and_subtitle_are_planned_together(self):
        root = "/TV/Modern Family (2009)"
        entries = [
            Entry(f"{root}/Season 01/Modern.Family.S01E01.Pilot.1080p.BluRay.x265.DTS.mkv", size=100),
            Entry(f"{root}/Season 01/Modern.Family.S01E01.chs.sup", size=20),
        ]
        plan = make_plan(entries, root, "openlist", NamingPolicy())
        self.assertFalse(plan.conflicts)
        self.assertEqual(len(plan.operations), 2)
        targets = {operation.kind: operation.target for operation in plan.operations}
        self.assertTrue(targets["rename_video"].endswith("Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.mkv"))
        self.assertTrue(targets["rename_subtitle"].endswith("Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.zh-CN.sup"))

    def test_missing_episode_year_is_skipped(self):
        root = "/TV/Unknown Show/Season 01"
        entries = [Entry(f"{root}/Unknown.Show.S01E01.1080p.WEB-DL.mkv")]
        plan = make_plan(entries, root, "openlist", NamingPolicy())
        self.assertEqual(plan.operations, [])
        self.assertEqual(plan.skipped[0]["reason"], "series_year_missing")

    def test_existing_target_is_a_conflict(self):
        root = "/TV/Modern Family (2009)/Season 01"
        target = f"{root}/Modern.Family.2009.S01E01.1080p.WEB-DL.mkv"
        entries = [
            Entry(f"{root}/Modern.Family.S01E01.1080p.WEB-DL.mkv"),
            Entry(target),
        ]
        plan = make_plan(entries, root, "openlist", NamingPolicy())
        self.assertTrue(any("Target exists" in conflict for conflict in plan.conflicts))

    def test_root_folder_rename_is_planned_after_media_files(self):
        root = "/Movies/Kill Bill 1"
        entries = [Entry(f"{root}/杀死比尔.2003.2160p.REMUX.DV.mkv")]
        plan = make_plan(
            entries,
            root,
            "openlist",
            NamingPolicy(),
            "Kill Bill: Vol. 1",
            2003,
            True,
            24,
            "en",
        )
        self.assertEqual([operation.kind for operation in plan.operations], ["rename_video", "rename_directory"])
        self.assertEqual(plan.operations[-1].target, "/Movies/Kill Bill Vol.1 (2003) {tmdb=24}")

    def test_tmdb_id_makes_root_rename_the_default_and_keeps_season_folder(self):
        root = "/TV/Modern Family"
        entries = [
            Entry(f"{root}/第一季/Modern.Family.S01E01.Pilot.1080p.BluRay.mkv"),
        ]
        plan = make_plan(
            entries,
            root,
            "openlist",
            NamingPolicy(),
            "Modern Family",
            2009,
            tmdb_id=1421,
            primary_language="en",
        )
        targets = [operation.target for operation in plan.operations]
        self.assertIn(
            f"{root}/第一季/Modern.Family.2009.S01E01.1080p.BluRay.mkv",
            targets,
        )
        self.assertIn("/TV/Modern Family (2009) {tmdb=1421}", targets)
        self.assertFalse(any("Season 01" in target for target in targets))


if __name__ == "__main__":
    unittest.main()
