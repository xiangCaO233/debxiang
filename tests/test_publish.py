import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import publish as publish_script  # noqa: E402


class PublishRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.site = self.root / "site"
        pool = self.site / "pool/main"
        pool.mkdir(parents=True)
        self.package = pool / "uv_1.2.3-1_amd64.deb"
        self.package.write_bytes(b"tiny deb fixture\n")
        self.tag = "uv-1.2.3-1"
        self.checksum_name = self.package.name + ".sha256"

    def tearDown(self):
        self.temporary.cleanup()

    def _publish_with(self, assets, *, downloaded_checksum="local", release_exists=True):
        calls = self.calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            self.assertEqual(args[:2], ("gh", "release"))
            action = args[2]
            if action == "list":
                releases = [{"tagName": self.tag}] if release_exists else []
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(releases))
            if action == "view":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=json.dumps({"assets": [{"name": name} for name in assets]}),
                )
            if action == "download":
                pattern = args[args.index("--pattern") + 1]
                destination = Path(args[args.index("--dir") + 1]) / pattern
                if pattern == self.checksum_name:
                    if downloaded_checksum == "local":
                        source = self.site / "checksums" / self.checksum_name
                        shutil.copy2(source, destination)
                    else:
                        destination.write_text(downloaded_checksum)
                elif pattern == self.package.name:
                    shutil.copy2(self.package, destination)
                else:
                    self.fail(f"unexpected download pattern: {pattern}")
                return subprocess.CompletedProcess(args, 0, stdout="")
            if action in ("upload", "create"):
                return subprocess.CompletedProcess(args, 0, stdout="")
            self.fail(f"unexpected gh action: {action}")

        environment = {
            "GITHUB_REPOSITORY": "example/debxiang",
            "GITHUB_SHA": "0123456789abcdef",
        }
        with (
            mock.patch.object(publish_script, "run", side_effect=fake_run),
            mock.patch.dict(os.environ, environment, clear=False),
        ):
            publish_script.publish(self.site)
        return calls

    @staticmethod
    def _actions(calls, action):
        return [call for call in calls if call[2] == action]

    def test_existing_matching_package_and_checksum_uploads_nothing(self):
        calls = self._publish_with({self.package.name, self.checksum_name})

        self.assertEqual(self._actions(calls, "upload"), [])
        self.assertEqual(self._actions(calls, "create"), [])
        downloads = self._actions(calls, "download")
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0][downloads[0].index("--pattern") + 1], self.checksum_name)

    def test_existing_wrong_checksum_raises_without_upload(self):
        with self.assertRaisesRegex(ValueError, "Published asset checksum differs"):
            self._publish_with(
                {self.package.name, self.checksum_name},
                downloaded_checksum="0" * 64 + "  " + self.package.name + "\n",
            )
        self.assertEqual(self._actions(self.calls, "upload"), [])
        self.assertEqual(self._actions(self.calls, "create"), [])

    def test_existing_checksum_with_missing_package_uploads_only_package(self):
        calls = self._publish_with({self.checksum_name})

        uploads = self._actions(calls, "upload")
        self.assertEqual(len(uploads), 1)
        self.assertEqual(Path(uploads[0][4]), self.package)
        self.assertNotIn("--clobber", uploads[0])
        self.assertEqual(self._actions(calls, "create"), [])

    def test_first_release_creates_with_package_and_checksum(self):
        calls = self._publish_with(set(), release_exists=False)

        creates = self._actions(calls, "create")
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][3], self.tag)
        self.assertEqual(Path(creates[0][4]), self.package)
        self.assertEqual(Path(creates[0][5]), self.site / "checksums" / self.checksum_name)
        self.assertEqual(self._actions(calls, "upload"), [])
        self.assertEqual(self._actions(calls, "view"), [])
        self.assertEqual(self._actions(calls, "download"), [])


if __name__ == "__main__":
    unittest.main()
