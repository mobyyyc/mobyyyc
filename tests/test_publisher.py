import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "profile_publisher", ROOT / "scripts/publish_profile.py"
)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    def test_unrelated_changes_block_publication(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(
            publisher, "git", side_effect=["scripts/update_profile.py", ""]
        ), patch.object(publisher.urllib.request, "urlopen") as network:
            with self.assertRaisesRegex(SystemExit, "outside the generated"):
                publisher.main()
            network.assert_not_called()

    def test_no_changes_do_not_create_a_commit(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(
            publisher, "git", return_value=""
        ), patch.object(publisher.urllib.request, "urlopen") as network:
            publisher.main()
            network.assert_not_called()

    def test_local_runs_cannot_publish(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), patch.object(
            publisher.urllib.request, "urlopen"
        ) as network:
            with self.assertRaisesRegex(SystemExit, "only supported"):
                publisher.main()
            network.assert_not_called()

    def test_traversal_and_non_svg_assets_are_not_allowed(self):
        for path in [
            "assets/../README.md",
            "assets/token.txt",
            ".github/workflows/update-profile.yml",
            "data/private.json",
        ]:
            self.assertFalse(publisher.allowed(path))


if __name__ == "__main__":
    unittest.main()
