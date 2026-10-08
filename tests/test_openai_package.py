import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import build as build_script  # noqa: E402
import openai_package  # noqa: E402


class OpenAIPackageTests(unittest.TestCase):
    required_tools = ("dpkg-deb", "gpg")

    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in cls.required_tools if shutil.which(tool) is None]
        if missing:
            raise unittest.SkipTest("missing OpenAI package test tools: " + ", ".join(missing))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.signer_home = self.root / "signer"
        self.signer_home.mkdir(mode=0o700)
        subprocess.run(
            [
                "gpg",
                "--homedir",
                str(self.signer_home),
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "OpenAI fixture <openai@example.invalid>",
                "ed25519",
                "sign",
                "0",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        listing = subprocess.run(
            ["gpg", "--homedir", str(self.signer_home), "--with-colons", "--list-keys"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.fingerprint = next(
            line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
        )
        self.key = self.root / "repository-signing-key.gpg"
        with self.key.open("wb") as output:
            subprocess.run(
                ["gpg", "--homedir", str(self.signer_home), "--export", self.fingerprint],
                check=True,
                stdout=output,
                stderr=subprocess.PIPE,
            )

        self.package_bytes = b"official package fixture bytes\n"
        self.package_digest = hashlib.sha256(self.package_bytes).hexdigest()
        self.packages = self.root / "Packages"
        self.packages.write_text(
            "Package: chatgpt\n"
            "Version: 26.1002.52244\n"
            "Architecture: amd64\n"
            "Filename: pool/main/c/chatgpt/chatgpt_26.1002.52244_amd64.deb\n"
            f"Size: {len(self.package_bytes)}\n"
            f"SHA256: {self.package_digest}\n"
            "Description: signed local fixture\n\n"
        )
        packages_digest = hashlib.sha256(self.packages.read_bytes()).hexdigest()
        self.release = self.root / "Release"
        self.release.write_text(
            "Suite: stable\n"
            "Codename: stable\n"
            "SHA256:\n"
            f" {packages_digest} {self.packages.stat().st_size} main/binary-amd64/Packages\n"
        )
        self.inrelease = self.root / "InRelease"
        subprocess.run(
            [
                "gpg",
                "--homedir",
                str(self.signer_home),
                "--batch",
                "--yes",
                "--local-user",
                self.fingerprint,
                "--clearsign",
                "--output",
                str(self.inrelease),
                str(self.release),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _local_download(self, url, target, expected_sha256=None):
        sources = {
            openai_package.KEY_URL: self.key,
            openai_package.BASE + "dists/stable/InRelease": self.inrelease,
            openai_package.BASE + "dists/stable/main/binary-amd64/Packages": self.packages,
        }
        source = sources[url]
        shutil.copy2(source, target)
        actual = hashlib.sha256(Path(target).read_bytes()).hexdigest()
        if expected_sha256 and actual != expected_sha256:
            Path(target).unlink()
            raise ValueError("SHA256 mismatch for local fixture")
        return actual

    def _discover(self, fingerprint=None):
        with (
            mock.patch.object(openai_package, "FINGERPRINT", fingerprint or self.fingerprint),
            mock.patch.object(openai_package, "download", side_effect=self._local_download),
        ):
            return openai_package.discover_chatgpt()

    def test_discovery_verifies_signed_release_and_returns_official_package(self):
        metadata = self._discover()

        self.assertEqual(metadata["version"], "26.1002.52244")
        self.assertEqual(metadata["deb_version"], "26.1002.52244")
        self.assertEqual(
            metadata["url"],
            openai_package.BASE + "pool/main/c/chatgpt/chatgpt_26.1002.52244_amd64.deb",
        )
        self.assertEqual(metadata["sha256"], self.package_digest)
        self.assertEqual(metadata["size"], len(self.package_bytes))
        self.assertEqual(metadata["upstream_signing_fingerprint"], self.fingerprint)

    def test_discovery_rejects_unreviewed_signing_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "fingerprint changed"):
            self._discover("0" * 40)

    def test_discovery_rejects_packages_tampered_after_release_was_signed(self):
        self.packages.write_text(self.packages.read_text().replace("26.1002.52244", "26.1002.52245"))

        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self._discover()

    def test_build_preserves_the_official_deb_byte_for_byte(self):
        package_root = self.root / "package-root"
        control = package_root / "DEBIAN/control"
        control.parent.mkdir(parents=True)
        control.write_text(
            "Package: chatgpt\n"
            "Version: 26.1002.52244\n"
            "Architecture: amd64\n"
            "Maintainer: OpenAI fixture <openai@example.invalid>\n"
            "Description: tiny real deb fixture\n"
        )
        executable = package_root / "usr/bin/chatgpt"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        official = self.root / "official.deb"
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(package_root), str(official)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        digest = hashlib.sha256(official.read_bytes()).hexdigest()
        output = self.root / "output"
        output.mkdir()

        def local_package_download(url, target, expected_sha256=None):
            self.assertEqual(url, "https://example.invalid/chatgpt.deb")
            shutil.copy2(official, target)
            actual = hashlib.sha256(Path(target).read_bytes()).hexdigest()
            self.assertEqual(expected_sha256, actual)
            return actual

        metadata = {
            "version": "26.1002.52244",
            "deb_version": "26.1002.52244",
            "url": "https://example.invalid/chatgpt.deb",
            "sha256": digest,
            "size": official.stat().st_size,
        }
        with mock.patch.object(build_script, "download", side_effect=local_package_download):
            build_script.build("chatgpt", metadata, output)

        mirrored = output / "chatgpt_26.1002.52244_amd64.deb"
        self.assertEqual(mirrored.read_bytes(), official.read_bytes())


if __name__ == "__main__":
    unittest.main()
