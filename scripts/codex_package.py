"""Discover the official stable CLI bundle and preserve its resource layout."""
import json
import os
from pathlib import Path
import re
import tarfile
from common import get_json, version

TARGET = "x86_64-unknown-linux-musl"


def discover_codex():
    release = get_json("https://api.github.com/repos/openai/codex/releases/latest", api=True)
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Codex latest release is not stable")
    tag = release["tag_name"]
    if not re.fullmatch(r"rust-v\d+\.\d+\.\d+", tag):
        raise ValueError("Unexpected stable Codex tag")
    name = f"codex-package-{TARGET}.tar.gz"
    assets = [asset for asset in release["assets"] if asset["name"] == name]
    if len(assets) != 1:
        raise ValueError("Expected one complete Codex Linux bundle")
    asset = assets[0]
    digest = asset.get("digest") or ""
    if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("Codex bundle has no valid upstream SHA256 digest")
    return {"version": version(tag.removeprefix("rust-v")), "tag": tag,
            "url": asset["browser_download_url"], "sha256": digest[7:].lower()}


def install_codex_bundle(archive, root, metadata):
    bundle = root / "usr/lib/codex-cli"
    bundle.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(bundle, filter="data")
    manifest = json.loads((bundle / "codex-package.json").read_text())
    expected = {"layoutVersion": 1, "version": metadata["version"], "target": TARGET,
                "variant": "codex", "entrypoint": "bin/codex",
                "resourcesDir": "codex-resources", "pathDir": "codex-path"}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("Codex bundle manifest differs from the selected release/layout")
    for relative in ("bin/codex", "bin/codex-code-mode-host", "codex-path/rg", "codex-resources/bwrap"):
        executable = bundle / relative
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f"Missing executable in Codex bundle: {relative}")
    (root / "usr/bin").mkdir(parents=True, exist_ok=True)
    (root / "usr/bin/codex").symlink_to("../lib/codex-cli/bin/codex")
    return bundle
