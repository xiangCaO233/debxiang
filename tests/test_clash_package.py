from pathlib import Path
import sys
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import clash_package  # noqa: E402


class ClashPackageTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.release = {
            "tag_name": "v2.5.8",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": "Clash.Verge_2.5.8_arm64.deb", "size": 100,
                 "digest": "sha256:" + self.digest, "browser_download_url": "https://example.invalid/arm.deb"},
                {"name": "Clash.Verge_2.5.8_amd64.deb", "size": 1234,
                 "digest": "sha256:" + self.digest, "browser_download_url": "https://example.invalid/amd64.deb"},
                {"name": "Clash.Verge_2.5.8_amd64.deb.sig", "size": 20,
                 "digest": "sha256:" + self.digest, "browser_download_url": "https://example.invalid/sig"},
            ],
        }

    def discover(self):
        with mock.patch.object(clash_package, "get_json", return_value=self.release) as get_json:
            result = clash_package.discover_clash()
        get_json.assert_called_once_with(clash_package.RELEASE_API, api=True)
        return result

    def test_selects_only_matching_amd64_deb_and_maps_metadata(self):
        metadata = self.discover()
        self.assertEqual(metadata, {
            "version": "2.5.8", "deb_version": "2.5.8",
            "url": "https://example.invalid/amd64.deb", "sha256": self.digest,
            "size": 1234, "package_name": "clash-verge",
        })

    def test_tracks_supported_upstream_semver(self):
        self.release["tag_name"] = "v2.10.3"
        self.release["assets"][1]["name"] = "Clash.Verge_2.10.3_amd64.deb"
        metadata = self.discover()
        self.assertEqual(metadata["version"], "2.10.3")
        self.assertEqual(metadata["deb_version"], "2.10.3")

    def test_rejects_non_stable_release(self):
        for flag in ("draft", "prerelease"):
            with self.subTest(flag=flag):
                self.release[flag] = True
                with mock.patch.object(clash_package, "get_json", return_value=self.release):
                    with self.assertRaisesRegex(ValueError, "not stable"):
                        clash_package.discover_clash()
                self.release[flag] = False

    def test_rejects_non_semver_tag(self):
        self.release["tag_name"] = "v2.5.8-rc.1"
        with mock.patch.object(clash_package, "get_json", return_value=self.release):
            with self.assertRaisesRegex(ValueError, "stable release version"):
                clash_package.discover_clash()

    def test_rejects_missing_or_malformed_digest(self):
        asset = self.release["assets"][1]
        for digest in (None, "", "sha256:abc", "sha512:" + self.digest):
            with self.subTest(digest=digest):
                if digest is None:
                    asset.pop("digest", None)
                else:
                    asset["digest"] = digest
                with mock.patch.object(clash_package, "get_json", return_value=self.release):
                    with self.assertRaisesRegex(ValueError, "valid SHA256 digest"):
                        clash_package.discover_clash()
                asset["digest"] = "sha256:" + self.digest

    def test_rejects_missing_or_invalid_size(self):
        asset = self.release["assets"][1]
        for size in (None, 0, -1, True, "1234"):
            with self.subTest(size=size):
                if size is None:
                    asset.pop("size", None)
                else:
                    asset["size"] = size
                with mock.patch.object(clash_package, "get_json", return_value=self.release):
                    with self.assertRaisesRegex(ValueError, "valid size"):
                        clash_package.discover_clash()
                asset["size"] = 1234

    def test_requires_exactly_one_asset_for_release_version(self):
        self.release["assets"].append(dict(self.release["assets"][1]))
        with mock.patch.object(clash_package, "get_json", return_value=self.release):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                clash_package.discover_clash()


if __name__ == "__main__":
    unittest.main()
