import base64
import contextlib
import io
import json
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

    def test_signed_publication_uses_schema_branch_and_expected_head(self):
        environment = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "mobyyyc/mobyyyc",
            "GITHUB_REF_NAME": "main",
            "GITHUB_TOKEN": "test-token",
        }
        result = {
            "data": {
                "createCommitOnBranch": {
                    "commit": {
                        "oid": "new-head",
                        "url": "https://github.com/mobyyyc/mobyyyc/commit/new-head",
                        "signature": {"isValid": True},
                    }
                }
            }
        }
        with patch.dict(os.environ, environment), patch.object(
            publisher, "git", side_effect=["README.md", "", "expected-head"]
        ), patch.object(
            publisher.urllib.request, "urlopen"
        ) as network, contextlib.redirect_stdout(
            io.StringIO()
        ):
            network.return_value.__enter__.return_value = io.StringIO(
                json.dumps(result)
            )
            publisher.main()
        request = network.call_args.args[0]
        payload = json.loads(request.data)["variables"]["input"]
        self.assertEqual(
            payload["branch"],
            {
                "repositoryNameWithOwner": "mobyyyc/mobyyyc",
                "branchName": "main",
            },
        )
        self.assertEqual(payload["expectedHeadOid"], "expected-head")
        self.assertEqual(
            [file["path"] for file in payload["fileChanges"]["additions"]],
            ["README.md"],
        )
        self.assertEqual(
            base64.b64decode(payload["fileChanges"]["additions"][0]["contents"]),
            (ROOT / "README.md").read_bytes(),
        )

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
