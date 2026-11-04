"""验证 Zig 缓存解包不降低原有包哈希和路径边界约束。"""
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ghostty_package


class GhosttyCacheTests(unittest.TestCase):
    """覆盖 0.17 缓存包装目录、损坏缓存以及恶意归档。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache/p"
        self.cache.mkdir(parents=True)
        self.expected = "fixture-1.0.0-verified-hash"
        self.target = self.cache / self.expected

    def archive(self, relative):
        """只生成隔离的最小缓存，不需要网络或真实编译器。"""
        with tarfile.open(self.cache / (self.expected + ".tar.gz"), "w:gz") as output:
            member = tarfile.TarInfo(relative)
            data = b"verified package manifest\n"
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))

    def fake_compiler(self, *args, **kwargs):
        # 校验器替身只认可包根的完整内容，外层目录名称不构成有效身份。
        directory = Path(args[2])
        manifest = directory / "build.zig.zon"
        valid = manifest.is_file() and manifest.read_bytes() == b"verified package manifest\n"
        return subprocess.CompletedProcess(args, 0, stdout=(self.expected if valid else "different-hash") + "\n")

    def test_wrapped_cache_is_verified_before_install_and_retry(self):
        self.archive(self.expected + "/upstream-root/build.zig.zon")
        with mock.patch.object(ghostty_package, "run", side_effect=self.fake_compiler):
            ghostty_package.materialize_zig_cache(Path("zig"), self.cache, {})
            self.assertTrue((self.target / "build.zig.zon").is_file())
            self.assertFalse((self.target / "upstream-root").exists())
            ghostty_package.materialize_zig_cache(Path("zig"), self.cache, {})

    def test_mismatched_content_never_becomes_a_trusted_directory(self):
        self.archive(self.expected + "/different-file.txt")
        with mock.patch.object(ghostty_package, "run", side_effect=self.fake_compiler):
            with self.assertRaisesRegex(ValueError, "Zig package hash differs"):
                ghostty_package.materialize_zig_cache(Path("zig"), self.cache, {})
        self.assertFalse(self.target.exists())

    def test_existing_corrupt_directory_is_not_overwritten(self):
        self.archive(self.expected + "/build.zig.zon")
        self.target.mkdir()
        sentinel = self.target / "keep-for-diagnosis"
        sentinel.write_bytes(b"corrupt cache")
        with mock.patch.object(ghostty_package, "run", side_effect=self.fake_compiler):
            with self.assertRaisesRegex(ValueError, "Existing Zig package hash differs"):
                ghostty_package.materialize_zig_cache(Path("zig"), self.cache, {})
        self.assertEqual(sentinel.read_bytes(), b"corrupt cache")

    def test_tar_traversal_is_rejected_before_compiler_verification(self):
        self.archive("../../escape.txt")
        with mock.patch.object(ghostty_package, "run") as compiler:
            with self.assertRaises(tarfile.FilterError):
                ghostty_package.materialize_zig_cache(Path("zig"), self.cache, {})
            compiler.assert_not_called()
        self.assertFalse((self.root / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
