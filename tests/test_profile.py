import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "profile_builder", ROOT / "scripts/update_profile.py"
)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def calendar_fixture(counts=(0, 3)):
    cells = []
    for i, count in enumerate(counts):
        day = f"2026-10-{i+1:02}"
        level = 0 if count == 0 else 2
        caption = (
            "No contributions"
            if count == 0
            else f"{count:,} contribution" + ("s" if count != 1 else "")
        )
        cells.append(
            f'<td id="d{i}" data-date="{day}" data-level="{level}"></td><tool-tip for="d{i}">{caption} on October {i+1}.</tool-tip>'
        )
    return f"<h2>{sum(counts):,} contributions in the last year</h2>" + "".join(cells)


class CalendarTests(unittest.TestCase):
    def test_accessible_tooltips_and_thousands(self):
        days = profile.parse_calendar(calendar_fixture((0, 1234)), minimum_days=2)
        self.assertEqual([d["count"] for d in days], [0, 1234])

    def test_missing_counts_fail_before_replacing_assets(self):
        with self.assertRaisesRegex(ValueError, "Missing contribution"):
            profile.parse_calendar(
                calendar_fixture().replace("3 contributions", "unavailable"),
                minimum_days=2,
            )

    def test_wrong_annual_total_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Annual total"):
            profile.parse_calendar(
                calendar_fixture().replace("<h2>3", "<h2>4"), minimum_days=2
            )

    def test_date_gap_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "date gap"):
            profile.parse_calendar(
                calendar_fixture().replace("2026-10-02", "2026-10-03"), minimum_days=2
            )

    def test_repo_pagination_forks_and_ownership(self):
        repo = {"fork": False, "stargazers_count": 1, "owner": {"login": "mobyyyc"}}
        pages = [
            ([repo] * 98)
            + [
                dict(repo, fork=True, stargazers_count=100),
                dict(repo, owner={"login": "other"}, stargazers_count=100),
            ],
            [dict(repo, stargazers_count=5)],
        ]
        fixture = calendar_fixture((0, 2))
        responses = [
            {"followers": 8},
            fixture,
            *pages,
            {"total_count": 4, "incomplete_results": False},
        ]
        with patch.object(
            profile, "request", side_effect=responses
        ) as fetch, patch.object(
            profile,
            "parse_calendar",
            return_value=profile.parse_calendar(fixture, minimum_days=2),
        ):
            data = profile.fetch_profile()
        self.assertEqual(data["stars"], 103)
        self.assertEqual(data["star_target"], 250)
        self.assertIn("page=2", fetch.call_args_list[3].args[0])

    def test_incomplete_pr_search_is_rejected(self):
        fixture = calendar_fixture()
        with patch.object(
            profile,
            "request",
            side_effect=[{"followers": 8}, fixture, [], {"incomplete_results": True}],
        ), patch.object(
            profile,
            "parse_calendar",
            return_value=profile.parse_calendar(fixture, minimum_days=2),
        ):
            with self.assertRaisesRegex(ValueError, "Incomplete PR"):
                profile.fetch_profile()


class ArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data/profile.json").read_text())
        cls.output = profile.build(cls.data)

    def test_milestone_boundary_advances(self):
        for count, target in [
            (0, 10),
            (9, 10),
            (10, 50),
            (50, 100),
            (100, 250),
            (10000, 20000),
        ]:
            self.assertEqual(profile.next_milestone(count), target)
        with self.assertRaises(ValueError):
            profile.next_milestone(-1)

    def test_zero_stars_does_not_invent_progress(self):
        data = copy.deepcopy(self.data)
        data.update(stars=0, star_target=10)
        root = ET.fromstring(profile.stars_svg(data, "light"))
        bar = root.find('.//{http://www.w3.org/2000/svg}rect[@class="earned"]')
        self.assertEqual(float(bar.attrib["width"]), 0)

    def test_every_svg_is_valid_accessible_and_self_contained(self):
        assets = [
            (name, text) for name, text in self.output.items() if name.endswith(".svg")
        ]
        self.assertEqual(len(assets), 12)
        for name, text in assets:
            with self.subTest(name=name):
                root = ET.fromstring(text)
                self.assertIsNotNone(root.find("{http://www.w3.org/2000/svg}title"))
                self.assertIsNotNone(root.find("{http://www.w3.org/2000/svg}desc"))
                self.assertNotIn("<script", text)
                self.assertNotIn("<image", text)
                if "@keyframes" in text or "<animate" in text:
                    self.assertIn("prefers-reduced-motion:reduce", text)

    def test_readme_points_to_real_assets_and_matches_data(self):
        readme = self.output["README.md"]
        images = re.findall(
            r'(?:src|srcset)="https://raw.githubusercontent.com/mobyyyc/mobyyyc/main/(assets/[^?"]+)\?v=([a-f0-9]{12})"',
            readme,
        )
        self.assertEqual(len(images), 12)
        for path, version in images:
            self.assertIn(path, self.output)
            self.assertEqual(
                version, hashlib.sha256(self.output[path].encode()).hexdigest()[:12]
            )
        self.assertIn(f"{self.data['contributions']:,} contributions", readme)
        self.assertIn(self.data["as_of"], readme)
        for field in [
            "Qiyuan Cai",
            "Computer Science",
            "AI Specialization",
            "University of Waterloo",
        ]:
            self.assertIn(field, readme)

    def test_calendar_wave_preserves_every_cell_and_its_color(self):
        ns = "{http://www.w3.org/2000/svg}"
        cells, _, _ = profile.grid_layout(self.data["days"])
        for theme in profile.THEMES:
            root = ET.fromstring(profile.calendar_svg(self.data, theme))
            days = root.findall(f'.//{ns}rect[@class="day"]')
            self.assertEqual(len(days), len(cells))
            delays = []
            for rect, cell in zip(days, cells):
                self.assertEqual(
                    rect.attrib["fill"], profile.THEMES[theme]["levels"][cell["level"]]
                )
                self.assertNotIn("opacity", rect.attrib)
                delays.append(
                    float(re.search(r"delay:([\d.-]+)s", rect.attrib["style"])[1])
                )
            self.assertGreater(delays[7], delays[0])  # Wave sweeps left to right.
            self.assertGreater(delays[1], delays[0])  # The leading edge is tilted.
            css = root.find(f"{ns}style").text
            self.assertIn("infinite", css)
            self.assertIn("translateY(-4px)", css)
            self.assertNotIn("opacity", css)
            self.assertNotIn("fill:", css.split("@keyframes")[1])

    def test_header_paths_flow_and_loop_without_a_jump(self):
        ns = "{http://www.w3.org/2000/svg}"
        root = ET.fromstring(profile.hero("light"))
        animations = root.findall(f".//{ns}animate")
        self.assertEqual(len(animations), 26)
        for animation in animations:
            self.assertEqual(animation.attrib["attributeName"], "d")
            self.assertEqual(animation.attrib["repeatCount"], "indefinite")
            frames = animation.attrib["values"].split(";")
            self.assertEqual(frames[0], frames[-1])
            self.assertNotEqual(frames[0], frames[6])
            commands = [re.findall("[MC]", frame) for frame in frames]
            self.assertTrue(all(command == commands[0] for command in commands))
        css = root.find(f"{ns}style").text
        self.assertIn(".flow-motion{display:none}", css)
        self.assertIn(".flow-static{display:inline}", css)

    def test_tracked_assets_are_reproducible(self):
        for name, text in self.output.items():
            with self.subTest(name=name):
                self.assertEqual((ROOT / name).read_text(), text)


if __name__ == "__main__":
    unittest.main()
