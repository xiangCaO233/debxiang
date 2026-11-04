"""覆盖 NAS 挂载失败、不可变下载文件和签名入口的发布顺序。"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nas_publish


class NasPublishTests(unittest.TestCase):
    """用隔离目录验证 NAS 发布契约，不接触实际软件源。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.mount = self.root / "mount"
        self.mount.mkdir()
        self.destination = self.mount / "debxiang"
        self.site = self.root / "site"
        self.package = self.site / "pool/main/fixture_1_amd64.deb"
        self.package.parent.mkdir(parents=True)
        self.package.write_bytes(b"verified package bytes")
        self.inrelease = self.site / "dists/trixie/InRelease"
        self.inrelease.parent.mkdir(parents=True)
        self.inrelease.write_bytes(b"signed release")
        self.by_hash = self.site / "dists/trixie/main/binary-amd64/by-hash/SHA256/fixture"
        self.by_hash.parent.mkdir(parents=True)
        self.by_hash.write_bytes(b"package index")
        (self.site / "manifest.json").write_text('{}\n')
        (self.site / "debxiang.asc").write_text("public key")

    def publish(self):
        with mock.patch.object(nas_publish.os.path, "ismount", return_value=True):
            nas_publish.publish_nas(self.site, self.destination, self.mount)

    def test_missing_mount_and_destination_escape_do_not_create_output(self):
        # 挂载掉线时必须在写入前失败，不能悄悄回退到本机磁盘。
        with mock.patch.object(nas_publish.os.path, "ismount", return_value=False):
            with self.assertRaisesRegex(ValueError, "mount is not available"):
                nas_publish.publish_nas(self.site, self.destination, self.mount)
        with mock.patch.object(nas_publish.os.path, "ismount", return_value=True):
            for target in (self.mount, self.root / "escape"):
                with self.assertRaisesRegex(ValueError, "dedicated directory"):
                    nas_publish.publish_nas(self.site, target, self.mount)
        self.assertFalse(self.destination.exists())

    def test_retry_preserves_history_and_refuses_changed_bytes(self):
        self.publish()
        history = self.destination / "pool/main/old_0_amd64.deb"
        history.write_bytes(b"old package referenced by a cached index")
        self.publish()
        self.assertTrue(history.exists())
        self.package.write_bytes(b"different bytes under the same version")
        self.inrelease.write_bytes(b"must not become public")
        with self.assertRaisesRegex(ValueError, "Published NAS artifact differs"):
            self.publish()
        self.assertEqual((self.destination / "dists/trixie/InRelease").read_bytes(), b"signed release")
        self.assertFalse(list(self.destination.rglob(".upload-*")))

    def test_packages_and_by_hash_precede_signature_and_manifest(self):
        calls = []
        real_copy = nas_publish.copy_atomic

        def record(source, target, immutable=False):
            calls.append(source.relative_to(self.site).as_posix())
            return real_copy(source, target, immutable)

        with mock.patch.object(nas_publish, "copy_atomic", side_effect=record):
            self.publish()
        self.assertEqual(calls[-2:], ["dists/trixie/InRelease", "manifest.json"])
        self.assertLess(calls.index(self.package.relative_to(self.site).as_posix()), len(calls) - 2)
        self.assertLess(calls.index(self.by_hash.relative_to(self.site).as_posix()), len(calls) - 2)

    def test_partial_copy_does_not_replace_public_file(self):
        self.publish()
        self.inrelease.write_bytes(b"replacement")
        target = self.destination / "dists/trixie/InRelease"
        with mock.patch.object(nas_publish.shutil, "copyfileobj", side_effect=OSError("network disconnected")):
            with self.assertRaisesRegex(OSError, "network disconnected"):
                nas_publish.copy_atomic(self.inrelease, target)
        self.assertEqual(target.read_bytes(), b"signed release")
        self.assertFalse(list(target.parent.glob(".upload-*")))

    def test_pages_contains_migration_instructions_without_packages(self):
        pages = self.root / "pages"
        nas_publish.prepare_pages(self.site, pages, "https://gitea.example/assets/debxiang/")
        self.assertFalse((pages / "pool").exists())
        self.assertFalse((pages / "dists").exists())
        self.assertIn("https://gitea.example/assets/debxiang/ trixie main", (pages / "index.html").read_text(encoding="utf-8"))
        self.assertEqual((pages / "debxiang.asc").read_bytes(), (self.site / "debxiang.asc").read_bytes())
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            nas_publish.prepare_pages(self.site, self.root / "invalid", "http://untrusted.example/")

    def test_public_probe_checks_signed_metadata_and_package_size(self):
        # 不能仅凭 NAS 上文件可读就撤下旧站点，公网入口也必须提供相同内容。
        bodies = [self.inrelease.read_bytes(), (self.site / "debxiang.asc").read_bytes()]
        requests = []

        def respond(request, **kwargs):
            requests.append(request)
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = bodies.pop(0) if bodies else b""
            response.headers = {"Content-Length": str(self.package.stat().st_size)}
            return response

        with mock.patch.object(nas_publish, "urlopen", side_effect=respond):
            nas_publish.verify_public(self.site, "https://gitea.example/assets/debxiang/")
        self.assertEqual([request.get_method() for request in requests], ["GET", "GET", "HEAD"])

        with mock.patch.object(nas_publish, "urlopen") as opener:
            opener.return_value.__enter__.return_value.read.return_value = b"old cached release"
            with self.assertRaisesRegex(ValueError, "metadata differs"):
                nas_publish.verify_public(self.site, "https://gitea.example/assets/debxiang/")


if __name__ == "__main__":
    unittest.main()
