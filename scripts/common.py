"""Small standard-library helpers shared by discovery and packaging."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import urllib.error


def run(*args, **kwargs):
    if len(args) >= 2 and str(args[0]) in ("podman", "docker") and args[1] == "run":
        # Cancellation of a runner job can otherwise orphan a rootless
        # container living in a separate systemd scope.
        with tempfile.TemporaryDirectory(prefix="debxiang-container-") as temporary:
            cidfile = Path(temporary) / "cid"
            command = [str(args[0]), "run", "--cidfile", str(cidfile), *map(str, args[2:])]
            try:
                return subprocess.run(command, check=True, **kwargs)
            finally:
                if cidfile.exists():
                    container = cidfile.read_text().strip()
                    if re.fullmatch(r"[a-f0-9]{64}", container):
                        subprocess.run([str(args[0]), "rm", "--force", container], check=False,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def request(url, *, api=False, offset=0):
    headers = {"User-Agent": "debxiang", "Accept": "application/vnd.github+json" if api else "*/*"}
    # Credentials go only to GitHub API, never to arbitrary asset hosts.
    if api and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    if offset:
        headers["Range"] = f"bytes={offset}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)


def get_json(url, *, api=False):
    for attempt in range(4):
        try:
            with request(url, api=api) as response:
                return json.load(response)
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError,
                json.JSONDecodeError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 3:
                raise
            print(f"Retrying upstream index {url}: {error}", flush=True)
            time.sleep(3 * (attempt + 1))


def version(value):
    value = value.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"Not a stable release version: {value!r}")
    return value


def download(url, target, sha256=None):
    cache_root = os.environ.get("DEBXIANG_DOWNLOAD_CACHE")
    if cache_root and sha256:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
            raise ValueError("Invalid download SHA256")
        cache = Path(cache_root)
        cache.mkdir(parents=True, exist_ok=True)
        cached = cache / sha256.lower()
        # Recheck on every use; the cache is never a trust source. Partial
        # files survive a canceled build and resume in the next job.
        actual = _download(url, cached, sha256.lower())
        cached.touch()
        shutil.copy2(cached, target)
        entries = [p for p in cache.iterdir()
                   if re.fullmatch(r"[a-f0-9]{64}(?:\.part)?", p.name) and p.is_file()]
        total = sum(p.stat().st_size for p in entries)
        for old in sorted(entries, key=lambda p: p.stat().st_mtime):
            if total <= 1024**3:
                break
            if old != cached:
                total -= old.stat().st_size
                old.unlink()
        return actual
    return _download(url, target, sha256)


def _download(url, target, sha256=None):
    target = Path(target)
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".part")
        failures = 0
        while True:
            offset = temporary.stat().st_size if temporary.exists() else 0
            print(f"Downloading {url} (offset {offset})", flush=True)
            try:
                with request(url, offset=offset) as response:
                    if response.status == 206:
                        content_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                                     response.headers.get("Content-Range", ""))
                        if not content_range or int(content_range[1]) != offset:
                            raise ValueError("Unexpected HTTP Content-Range")
                        expected_total = int(content_range[3])
                        mode = "ab"
                    else:
                        expected_total = response.headers.get("Content-Length")
                        expected_total = int(expected_total) if expected_total else None
                        mode = "wb"  # Server may ignore Range; restart safely.
                    with temporary.open(mode) as output:
                        while chunk := response.read(1024 * 1024):
                            output.write(chunk)
                received = temporary.stat().st_size
                if expected_total is not None and received != expected_total:
                    raise ValueError(f"Incomplete download: expected {expected_total} bytes, received {received}")
                break
            except Exception as error:
                received = temporary.stat().st_size if temporary.exists() else 0
                # Large assets may require many short connections. Limit
                # consecutive failures without progress, rather than
                # discarding a steadily advancing multi-hundred-MB download.
                failures = 0 if received > offset else failures + 1
                if failures >= 5:
                    raise
                print(f"Download interrupted; resuming at {received} bytes "
                      f"({failures}/5 failures without progress): {error}", flush=True)
                time.sleep(3)
        temporary.replace(target)
    with target.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if sha256 and actual != sha256:
        target.unlink()
        raise ValueError(f"SHA256 mismatch: {url}; expected {sha256}, received {actual}")
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
