import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from codex_package import TARGET, discover_codex, install_codex_bundle


class CodexDiscoveryTests(unittest.TestCase):
    def release(self):
        return {"tag_name": "rust-v0.162.1", "draft": False, "prerelease": False,
                "assets": [{"name": f"codex-package-{TARGET}.tar.gz",
                            "digest": "sha256:" + "a" * 64,
                            "browser_download_url": "https://github.com/openai/codex/releases/download/rust-v0.162.1/bundle.tar.gz"}]}

    def test_complete_bundle_and_release_version(self):
        with mock.patch("codex_package.get_json", return_value=self.release()):
            result = discover_codex()
        self.assertEqual(result["version"], "0.162.1")
        self.assertEqual(result["sha256"], "a" * 64)

    def test_rejects_prerelease_and_missing_digest(self):
        for changes in ({"prerelease": True}, {"tag_name": "rust-v0.163.0-alpha.1"},
                        {"assets": []}, {"assets": [{"name": f"codex-package-{TARGET}.tar.gz", "digest": "sha256:bad"}]}):
            release = self.release() | changes
            with self.subTest(changes=changes), mock.patch("codex_package.get_json", return_value=release):
                with self.assertRaises(ValueError):
                    discover_codex()


class CodexLayoutTests(unittest.TestCase):
    def archive(self, path, version="0.162.1", escape=False):
        manifest = {"layoutVersion": 1, "version": version, "target": TARGET, "variant": "codex",
                    "entrypoint": "bin/codex", "resourcesDir": "codex-resources", "pathDir": "codex-path"}
        with tarfile.open(path, "w:gz") as tar:
            for name in ("bin/codex", "bin/codex-code-mode-host", "codex-resources/bwrap", "codex-path/rg", "codex-package.json"):
                data = json.dumps(manifest).encode() if name.endswith(".json") else b"#!/bin/sh\n"
                info = tarfile.TarInfo(name)
                info.mode = 0o755 if not name.endswith(".json") else 0o644
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            if escape:
                info = tarfile.TarInfo("../../../../escape")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))

    def test_preserves_resources_and_symlink_entrypoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bundle.tar.gz"
            self.archive(archive)
            bundle = install_codex_bundle(archive, root / "package", {"version": "0.162.1"})
            self.assertEqual((root / "package/usr/bin/codex").resolve(), bundle / "bin/codex")
            self.assertTrue((bundle / "codex-resources/bwrap").is_file())
            self.assertTrue((bundle / "codex-path/rg").is_file())

    def test_rejects_mismatched_manifest_and_archive_escape(self):
        for version, escape, expected in (("0.161.0", False, ValueError), ("0.162.1", True, tarfile.FilterError)):
            with self.subTest(version=version, escape=escape), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "bundle.tar.gz"
                self.archive(archive, version, escape)
                with self.assertRaises(expected):
                    install_codex_bundle(archive, root / "package", {"version": "0.162.1"})
