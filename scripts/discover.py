"""Track stable upstream versions; Ghostty publishes stable tags, not /latest."""
import json
import re
from common import get_json, version


def latest_tag(tags):
    stable = [version(t["name"]) for t in tags if re.fullmatch(r"v?\d+\.\d+\.\d+", t["name"])]
    if not stable:
        raise ValueError("No stable Ghostty tag found")
    return max(stable, key=lambda v: tuple(map(int, v.split("."))))


def discover_uv():
    uv = get_json("https://api.github.com/repos/astral-sh/uv/releases/latest", api=True)
    if uv.get("draft") or uv.get("prerelease"):
        raise ValueError("uv latest release is not stable")
    uv_version = version(uv["tag_name"])
    name = "uv-x86_64-unknown-linux-gnu.tar.gz"
    asset = next(a for a in uv["assets"] if a["name"] == name)
    checksum = next((a["browser_download_url"] for a in uv["assets"] if a["name"] == name + ".sha256"), None)
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:") and not checksum:
        raise ValueError("uv upstream release has no SHA256 digest or checksum asset")
    return {"version": uv_version, "url": asset["browser_download_url"],
            "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
            "checksum_url": checksum}


def discover_zig():
    zig = get_json("https://ziglang.org/download/index.json")
    zig_version = max((k for k in zig if re.fullmatch(r"\d+\.\d+\.\d+", k)),
                      key=lambda v: tuple(map(int, v.split("."))))
    zig_asset = zig[zig_version]["x86_64-linux"]
    return {"version": zig_version, "url": zig_asset["tarball"], "sha256": zig_asset["shasum"]}


def discover_ghostty():
    tags = get_json("https://api.github.com/repos/ghostty-org/ghostty/tags?per_page=100", api=True)
    ghostty = latest_tag(tags)
    return {"version": ghostty,
            "url": f"https://release.files.ghostty.org/{ghostty}/ghostty-{ghostty}.tar.gz"}


DISCOVERERS = {"uv": discover_uv, "zig": discover_zig, "ghostty": discover_ghostty}


def discover():
    return {name: fetch() for name, fetch in DISCOVERERS.items()}


if __name__ == "__main__":
    print(json.dumps(discover(), indent=2))
