"""Discover the original Clash Verge Rev amd64 Debian package."""
import re

from common import get_json, version


RELEASE_API = "https://api.github.com/repos/clash-verge-rev/clash-verge-rev/releases/latest"


def discover_clash():
    release = get_json(RELEASE_API, api=True)
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Clash Verge Rev latest release is not stable")

    upstream_version = version(release.get("tag_name", ""))
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Clash Verge Rev release has no asset list")

    # Match the published upstream naming scheme and bind the selected file to
    # the validated release tag. This excludes ARM packages and similarly
    # named auxiliary files.
    asset_pattern = re.compile(
        rf"^Clash\.Verge_(?P<version>{re.escape(upstream_version)})_amd64\.deb$"
    )
    candidates = [asset for asset in assets
                  if isinstance(asset, dict)
                  and isinstance(asset.get("name"), str)
                  and asset_pattern.fullmatch(asset["name"])]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one Clash Verge Rev amd64 DEB asset")

    asset = candidates[0]
    digest = asset.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("Clash Verge Rev amd64 DEB has no valid SHA256 digest")
    size = asset.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("Clash Verge Rev amd64 DEB has no valid size")
    url = asset.get("browser_download_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("Clash Verge Rev amd64 DEB has no valid download URL")

    # Verified against dpkg-deb -f on the upstream v2.5.8 amd64 artifact.
    # Upstream currently uses its release semver verbatim as the DEB Version.
    deb_version = upstream_version
    return {"version": upstream_version, "deb_version": deb_version,
            "url": url, "sha256": digest.removeprefix("sha256:").lower(),
            "size": size, "package_name": "clash-verge"}
