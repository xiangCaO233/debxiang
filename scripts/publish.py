"""Idempotent immutable per-package releases; retry missing assets only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from common import run


def publish(site):
    repository = os.environ["GITHUB_REPOSITORY"]
    releases = json.loads(run("gh", "release", "list", "--repo", repository, "--limit", "1000",
                             "--json", "tagName", capture_output=True, text=True).stdout)
    for package in sorted((site / "pool").rglob("*.deb")):
        name, version, _ = package.name.split("_", 2)
        # Debian versions allow '~', but Git tags explicitly forbid it.
        tag = f"{name}-{version.replace('~', '-').replace(':', '-')}"
        # List explicitly: a network/permission error must not be mistaken
        # for an absent release and cause an unrelated create attempt.
        with package.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        checksums = site / "checksums"
        checksums.mkdir(exist_ok=True)
        checksum_file = checksums / (package.name + ".sha256")
        checksum_file.write_text(digest + "  " + package.name + "\n")
        if any(r["tagName"] == tag for r in releases):
            details = json.loads(run("gh", "release", "view", tag, "--repo", repository,
                                    "--json", "assets", capture_output=True, text=True).stdout)
            checksum_asset = next((a for a in details["assets"] if a["name"] == checksum_file.name), None)
            if checksum_asset:
                with tempfile.TemporaryDirectory() as directory:
                    run("gh", "release", "download", tag, "--repo", repository,
                        "--pattern", checksum_file.name, "--dir", directory)
                    if (Path(directory) / checksum_file.name).read_text() != checksum_file.read_text():
                        raise ValueError(f"Published asset checksum differs: {tag}; increase packaging revision")
            if any(a["name"] == package.name for a in details["assets"]):
                # Verify the public artifact before accepting a retry; a lost
                # state directory must not silently change an existing asset.
                if checksum_asset:
                    continue
                with tempfile.TemporaryDirectory() as directory:
                    run("gh", "release", "download", tag, "--repo", repository,
                        "--pattern", package.name, "--dir", directory)
                    existing = Path(directory) / package.name
                    with existing.open("rb") as stream:
                        old_hash = hashlib.file_digest(stream, "sha256").digest()
                    with package.open("rb") as stream:
                        new_hash = hashlib.file_digest(stream, "sha256").digest()
                    if old_hash != new_hash:
                        raise ValueError(f"Published asset differs: {tag}; increase packaging revision")
                run("gh", "release", "upload", tag, checksum_file, "--repo", repository)
                continue
            # A failed create can leave either asset present. Never clobber it.
            assets = {a["name"] for a in details["assets"]}
            if checksum_file.name not in assets:
                run("gh", "release", "upload", tag, checksum_file, "--repo", repository)
            run("gh", "release", "upload", tag, package, "--repo", repository)
        else:
            run("gh", "release", "create", tag, package, checksum_file, "--repo", repository,
                # A long build may finish after the workflow file changes on
                # main. GITHUB_TOKEN cannot tag that older workflow commit;
                # using the publishing branch avoids extra workflow scopes.
                "--target", "main", "--title", f"{name} {version}",
                "--notes", "Maintained from verified upstream stable packages. See the APT manifest for source URLs and checksums.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    publish(parser.parse_args().site)
