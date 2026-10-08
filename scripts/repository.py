"""Create a signed APT repository from complete Debian packages."""
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import shutil
from functools import cmp_to_key
from common import run


def prune_packages(pool, keep=3):
    """Bound Pages storage; older versions remain in GitHub Releases."""
    groups = {}
    for path in pool.glob("*.deb"):
        name = run("dpkg-deb", "--field", path, "Package", capture_output=True, text=True).stdout.strip()
        package_version = run("dpkg-deb", "--field", path, "Version", capture_output=True, text=True).stdout.strip()
        groups.setdefault(name, []).append((package_version, path))

    def compare(a, b):
        import subprocess
        if a[0] == b[0]:
            return 0
        result = subprocess.run(["dpkg", "--compare-versions", a[0], "gt", b[0]], check=False)
        if result.returncode not in (0, 1):
            raise ValueError("Invalid Debian package version")
        return -1 if result.returncode == 0 else 1

    for name, group in groups.items():
        retained = 1 if name == "chatgpt" else keep
        for _, path in sorted(group, key=cmp_to_key(compare))[retained:]:
            path.unlink()


def signing_key(state):
    home = state / "gnupg"
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    key_file = state / "signing-key.txt"
    if not key_file.exists():
        existing = run("gpg", "--homedir", home, "--with-colons", "--list-secret-keys",
                       capture_output=True, text=True).stdout
        if "sec:" not in existing:
            run("gpg", "--homedir", home, "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                "--quick-generate-key", "debxiang APT signing key <noreply@github.com>", "ed25519", "sign", "0")
        keys = run("gpg", "--homedir", home, "--with-colons", "--list-secret-keys",
                   capture_output=True, text=True).stdout
        fingerprint = next(line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:"))
        key_file.write_text(fingerprint + "\n")
    return home, key_file.read_text().strip()


def make_repository(pool, site, state, manifest):
    site.mkdir(parents=True, exist_ok=True)
    destination = site / "pool/main"
    shutil.copytree(pool, destination, dirs_exist_ok=True)
    packages_dir = site / "dists/trixie/main/binary-amd64"
    packages_dir.mkdir(parents=True, exist_ok=True)
    with (packages_dir / "Packages").open("wb") as output:
        run("apt-ftparchive", "packages", "pool", cwd=site, stdout=output)
    with (packages_dir / "Packages").open("rb") as source:
        with (packages_dir / "Packages.gz").open("wb") as destination_stream:
            with gzip.GzipFile(fileobj=destination_stream, mode="wb", mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
    release_dir = site / "dists/trixie"
    with (release_dir / "Release").open("wb") as output:
        run("apt-ftparchive", "-o", "APT::FTPArchive::Release::Origin=debxiang",
            "-o", "APT::FTPArchive::Release::Label=debxiang",
            "-o", "APT::FTPArchive::Release::Suite=trixie",
            "-o", "APT::FTPArchive::Release::Codename=trixie",
            "-o", "APT::FTPArchive::Release::Architectures=amd64",
            "-o", "APT::FTPArchive::Release::Components=main",
            "release", "dists/trixie", cwd=site, stdout=output)
    home, key = signing_key(state)
    args = ["gpg", "--homedir", home, "--batch", "--yes", "--local-user", key]
    run(*args, "--clearsign", "--output", release_dir / "InRelease", release_dir / "Release")
    run(*args, "--armor", "--detach-sign", "--output", release_dir / "Release.gpg", release_dir / "Release")
    with (site / "debxiang.asc").open("wb") as output:
        run("gpg", "--homedir", home, "--armor", "--export", key, stdout=output)
    (site / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (site / ".nojekyll").touch()
    sums = []
    for package in sorted((site / "pool").rglob("*.deb")):
        with package.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        sums.append(f"{digest}  {package.relative_to(site)}")
    (site / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    (site / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>debxiang APT repository</title>'
        '<h1>debxiang</h1><p>Debian trixie / amd64: uv, zig, zig-stable, ghostty, chatgpt.</p>'
        '<p><a href="debxiang.asc">APT signing key</a> · '
        '<a href="manifest.json">Build manifest</a> · <a href="SHA256SUMS">Checksums</a></p>'
        '<p>Signing key fingerprint: <code>' + key + '</code></p>')
    return key
