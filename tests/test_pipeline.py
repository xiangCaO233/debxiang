import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from build import deb_version, package_version, required_zig  # noqa: E402
from common import extract  # noqa: E402
from discover import latest_tag  # noqa: E402
import maintain as maintain_script  # noqa: E402
from repository import make_repository, prune_packages  # noqa: E402


class DiscoveryTests(unittest.TestCase):
    def test_latest_tag_selects_highest_stable_semver(self):
        tags = [
            {"name": "tip"},
            {"name": "v1.9.9"},
            {"name": "1.10.0"},
            {"name": "v2.0.0-beta.1"},
            {"name": "release-99.0.0"},
        ]

        self.assertEqual(latest_tag(tags), "1.10.0")

    def test_latest_tag_rejects_list_without_stable_release(self):
        with self.assertRaisesRegex(ValueError, "No stable Ghostty tag"):
            latest_tag([{"name": "tip"}, {"name": "v1.2.3-rc1"}])


class PackagingVersionTests(unittest.TestCase):
    def test_ghostty_fix_is_newer_without_rebuilding_other_components(self):
        self.assertEqual(package_version({"version": "1.3.1"}, "ghostty"),
                         "1.3.1-100~debxiang2~trixie")
        self.assertEqual(package_version({"version": "0.12.23"}, "uv"),
                         "0.12.23-100~debxiang1~trixie")
        self.assertEqual(package_version({"version": "26.1002.52244",
                                          "deb_version": "26.1002.52244"}, "chatgpt"),
                         "26.1002.52244")


class RequiredZigTests(unittest.TestCase):
    def test_zon_version_takes_precedence(self):
        build_zig = 'const required_zig = "0.13.0";'
        zon = '.{ .minimum_zig_version = "0.15.2", }'

        self.assertEqual(required_zig(build_zig, zon), "0.15.2")

    def test_parses_legacy_string_constant(self):
        build_zig = (
            'const required_zig = '
            'std.SemanticVersion.parse("0.14.1") catch unreachable;'
        )

        self.assertEqual(required_zig(build_zig), "0.14.1")

    def test_parses_legacy_semantic_version_struct(self):
        build_zig = """
            const required_zig: std.SemanticVersion = .{
                .major = 0,
                .minor = 13,
                .patch = 0,
            };
        """

        self.assertEqual(required_zig(build_zig), "0.13.0")

    def test_rejects_missing_or_non_release_version(self):
        with self.assertRaisesRegex(ValueError, "Cannot determine"):
            required_zig("const something_else = true;", '.{ .minimum_zig_version = "master", }')


class ArchiveSafetyTests(unittest.TestCase):
    def test_extract_rejects_parent_path_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            archive = temporary_path / "malicious.tar.gz"
            destination = temporary_path / "destination"
            escaped = temporary_path / "escaped.txt"
            with tarfile.open(archive, "w:gz") as tar:
                safe = tarfile.TarInfo("package/README")
                safe_data = b"safe\n"
                safe.size = len(safe_data)
                tar.addfile(safe, io.BytesIO(safe_data))

                traversal = tarfile.TarInfo("../escaped.txt")
                traversal_data = b"escaped\n"
                traversal.size = len(traversal_data)
                tar.addfile(traversal, io.BytesIO(traversal_data))

            with self.assertRaises(tarfile.FilterError):
                extract(archive, destination)
            self.assertFalse(escaped.exists())

    def test_extract_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            archive = temporary_path / "malicious-link.tar.gz"
            destination = temporary_path / "destination"
            with tarfile.open(archive, "w:gz") as tar:
                root = tarfile.TarInfo("package")
                root.type = tarfile.DIRTYPE
                tar.addfile(root)

                link = tarfile.TarInfo("package/outside")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../outside"
                tar.addfile(link)

            with self.assertRaises(tarfile.FilterError):
                extract(archive, destination)


class SignedRepositoryTests(unittest.TestCase):
    required_tools = ("dpkg-deb", "apt-ftparchive", "gpg")

    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in cls.required_tools if shutil.which(tool) is None]
        if missing:
            raise unittest.SkipTest("missing repository test tools: " + ", ".join(missing))

    @staticmethod
    def _build_fixture(package_root, target, name="fixture-tool", version="1.2.3-1"):
        control = package_root / "DEBIAN/control"
        control.parent.mkdir(parents=True)
        control.write_text(
            f"Package: {name}\n"
            f"Version: {version}\n"
            "Architecture: amd64\n"
            "Maintainer: Tests <tests@example.invalid>\n"
            "Section: utils\n"
            "Priority: optional\n"
            "Description: real package fixture for repository tests\n"
        )
        executable = package_root / "usr/bin" / name
        executable.parent.mkdir(parents=True)
        executable.write_text(f"#!/bin/sh\nprintf '%s\\n' {name}-{version}\n")
        executable.chmod(0o755)
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(package_root), str(target)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    @staticmethod
    def _field(stanza, name):
        prefix = name + ": "
        return next(line.removeprefix(prefix) for line in stanza.splitlines() if line.startswith(prefix))

    def test_real_package_signed_repository_and_stable_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = root / "input-pool"
            pool.mkdir()
            package = pool / "fixture-tool_1.2.3-1_amd64.deb"
            self._build_fixture(root / "package-root", package)

            state = root / "state"
            state.mkdir()
            manifest = {"fixture-tool": {"version": "1.2.3"}}
            first_site = root / "site-first"
            second_site = root / "site-second"
            first_fingerprint = make_repository(pool, first_site, state, manifest)
            second_fingerprint = make_repository(pool, second_site, state, manifest)

            self.assertEqual(first_fingerprint, second_fingerprint)
            self.assertRegex(first_fingerprint, r"^[0-9A-F]{40}$")
            self.assertEqual((state / "signing-key.txt").read_text().strip(), first_fingerprint)

            published = first_site / "pool/main" / package.name
            package_digest = hashlib.sha256(published.read_bytes()).hexdigest()
            packages_path = first_site / "dists/trixie/main/binary-amd64/Packages"
            packages = packages_path.read_text()
            self.assertEqual(self._field(packages, "Package"), "fixture-tool")
            self.assertEqual(self._field(packages, "Filename"), f"pool/main/{package.name}")
            self.assertEqual(self._field(packages, "SHA256"), package_digest)

            with gzip.open(packages_path.with_suffix(".gz"), "rb") as compressed:
                self.assertEqual(compressed.read(), packages_path.read_bytes())

            sums = (first_site / "SHA256SUMS").read_text().splitlines()
            self.assertIn(f"{package_digest}  pool/main/{package.name}", sums)

            verify_home = root / "verify-gnupg"
            verify_home.mkdir(mode=0o700)
            subprocess.run(
                ["gpg", "--homedir", str(verify_home), "--batch", "--import",
                 str(first_site / "debxiang.asc")],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            imported = subprocess.run(
                ["gpg", "--homedir", str(verify_home), "--with-colons", "--list-keys"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout
            imported_fingerprints = [
                line.split(":")[9] for line in imported.splitlines() if line.startswith("fpr:")
            ]
            self.assertIn(first_fingerprint, imported_fingerprints)

            release = first_site / "dists/trixie/Release"
            subprocess.run(
                ["gpg", "--homedir", str(verify_home), "--batch", "--verify",
                 str(release.with_suffix(".gpg")), str(release)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["gpg", "--homedir", str(verify_home), "--batch", "--verify",
                 str(release.parent / "InRelease")],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def test_prune_packages_keeps_latest_three_versions_per_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = root / "pool"
            pool.mkdir()
            versions = {
                "numeric-order": ("1.8-1", "1.9-1", "1.10-1", "2.0-1"),
                "independent": ("3.0-1", "3.1-1", "3.2-1", "3.3-1"),
                "short-history": ("7.0-1", "7.1-1"),
                "chatgpt": ("26.902.1", "26.1002.1"),
            }
            expected = {
                "numeric-order": {"1.9-1", "1.10-1", "2.0-1"},
                "independent": {"3.1-1", "3.2-1", "3.3-1"},
                "short-history": {"7.0-1", "7.1-1"},
                "chatgpt": {"26.1002.1"},
            }

            fixture_number = 0
            for name, package_versions in versions.items():
                for version in package_versions:
                    fixture_number += 1
                    package = pool / f"{name}_{version}_amd64.deb"
                    self._build_fixture(
                        root / f"package-root-{fixture_number}",
                        package,
                        name=name,
                        version=version,
                    )

            prune_packages(pool, keep=3)

            retained = {}
            for package in pool.glob("*.deb"):
                name = subprocess.run(
                    ["dpkg-deb", "--field", str(package), "Package"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                version = subprocess.run(
                    ["dpkg-deb", "--field", str(package), "Version"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                retained.setdefault(name, set()).add(version)

            self.assertEqual(retained, expected)

    def test_discovery_failures_preserve_current_package_without_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            pool = state / "pool"
            pool.mkdir(parents=True)
            site = root / "site"
            github_output = root / "github-output"
            upstream = "1.2.3"
            package_version = deb_version(upstream)
            package = pool / f"uv_{package_version}_amd64.deb"
            self._build_fixture(
                root / "package-root",
                package,
                name="uv",
                version=package_version,
            )
            previous_manifest = {
                "uv": {
                    "version": upstream,
                    "url": "https://example.invalid/uv.tar.gz",
                    "sha256": "0" * 64,
                    "deb_version": package_version,
                }
            }
            (state / "manifest.json").write_text(json.dumps(previous_manifest) + "\n")

            def discovery_failure():
                raise OSError("simulated network failure")

            discoverers = {
                "uv": lambda: {
                    "version": upstream,
                    "url": "https://example.invalid/uv.tar.gz",
                    "sha256": "0" * 64,
                },
                "zig": discovery_failure,
                "ghostty": discovery_failure,
            }
            with (
                mock.patch.object(maintain_script, "DISCOVERERS", discoverers),
                mock.patch.object(
                    maintain_script,
                    "run",
                    side_effect=AssertionError("engine/build command must not run when pending is empty"),
                ) as run_mock,
                mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(github_output)}, clear=False),
            ):
                failures = maintain_script.maintain(state, site, "podman")

            run_mock.assert_not_called()
            self.assertEqual(len(failures), 2)
            self.assertTrue(any(item.startswith("zig discovery:") for item in failures))
            self.assertTrue(any(item.startswith("ghostty discovery:") for item in failures))
            self.assertIn("failures_count=2\nchanged=true\n", github_output.read_text())
            self.assertIn("manifest_sha256=" + hashlib.sha256((state / "manifest.json").read_bytes()).hexdigest(),
                          github_output.read_text())

            packages = (site / "dists/trixie/main/binary-amd64/Packages").read_text()
            self.assertEqual(self._field(packages, "Package"), "uv")
            self.assertEqual(self._field(packages, "Version"), package_version)
            self.assertEqual(self._field(packages, "Filename"), f"pool/main/{package.name}")
            self.assertTrue((site / "dists/trixie/InRelease").is_file())
            self.assertTrue((site / "dists/trixie/Release.gpg").is_file())
            self.assertEqual(json.loads((state / "manifest.json").read_text()), previous_manifest)
            self.assertEqual(json.loads((site / "manifest.json").read_text()), previous_manifest)
            self.assertEqual(json.loads((site / "failures.json").read_text()), failures)
            self.assertTrue(package.is_file())

    def test_unchanged_deployment_skips_upload_but_failed_deployment_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            pool = state / "pool"
            pool.mkdir(parents=True)
            metadata = {"version": "1.2.3", "sha256": "0" * 64,
                        "deb_version": deb_version("1.2.3")}
            self._build_fixture(root / "package-root", pool / f"uv_{metadata['deb_version']}_amd64.deb",
                                name="uv", version=metadata["deb_version"])
            manifest = state / "manifest.json"
            manifest.write_text(json.dumps({"uv": metadata}) + "\n")
            deployed = state / "deployed-manifest.sha256"
            deployed.write_text(hashlib.sha256(manifest.read_bytes()).hexdigest() + "\n")
            output = root / "github-output"
            site = root / "site"
            with (mock.patch.object(maintain_script, "DISCOVERERS", {"uv": lambda: metadata}),
                  mock.patch.object(maintain_script, "run", side_effect=AssertionError("No container needed")),
                  mock.patch.object(maintain_script, "make_repository", wraps=make_repository) as sign,
                  mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)})):
                self.assertEqual(maintain_script.maintain(state, site, "podman"), [])
                self.assertEqual(output.read_text(), "failures_count=0\nchanged=false\n")
                sign.assert_not_called()
                self.assertFalse(site.exists())
                # A changed state with no successful deployment must retry
                # publication even when every package is already built.
                deployed.write_text("0" * 64 + "\n")
                output.write_text("")
                self.assertEqual(maintain_script.maintain(state, site, "podman"), [])
                self.assertIn("changed=true\n", output.read_text())
                sign.assert_called_once()
                self.assertTrue((site / "dists/trixie/InRelease").is_file())


if __name__ == "__main__":
    unittest.main()
