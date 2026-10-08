"""Small standard-library helpers shared by discovery and packaging."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.request


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def request(url, *, api=False):
    headers = {"User-Agent": "debxiang", "Accept": "application/vnd.github+json" if api else "*/*"}
    # Credentials go only to GitHub API, never to arbitrary asset hosts.
    if api and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)


def get_json(url, *, api=False):
    with request(url, api=api) as response:
        return json.load(response)


def version(value):
    value = value.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"Not a stable release version: {value!r}")
    return value


def download(url, target, sha256=None):
    target = Path(target)
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".part")
        with request(url) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(target)
    with target.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if sha256 and actual != sha256:
        target.unlink()
        raise ValueError(f"SHA256 mismatch: {url}")
    return actual


def extract(archive, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # Reject escapes and links outside the destination, while preserving
        # executable bits and safe internal symlinks.
        tar.extractall(directory, filter="data")
    entries = list(directory.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        raise ValueError(f"Expected one upstream archive root: {archive}")
    return entries[0]
