"""Discover the original ChatGPT .deb through OpenAI's signed APT index."""
import hashlib
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import urljoin
from common import download, run

BASE = "https://persistent.oaistatic.com/codex-app-prod/linux/deb/"
# Reviewed fingerprint and immutable bootstrap key published by OpenAI's
# official Linux installer. A primary-key rotation requires review here.
FINGERPRINT = "3BFA0E4AE8B8CC16A2D9BA684A3B4A566C4660E4"
KEY_URL = "https://persistent.oaistatic.com/codex-app-prod/linux/repository-signing-key.gpg"


def paragraphs(text):
    result, fields, last_key = [], {}, None
    for line in text.splitlines() + [""]:
        if not line:
            if fields:
                result.append(fields)
            fields, last_key = {}, None
        elif line[0].isspace() and last_key:
            fields[last_key] += "\n" + line.strip()
        else:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
            last_key = key
    return result


def primary_fingerprints(output):
    keys, primary = [], False
    for line in output.splitlines():
        fields = line.split(":")
        if fields[0] == "pub":
            primary = True
        elif fields[0] == "sub":
            primary = False
        elif fields[0] == "fpr" and primary:
            keys.append(fields[9])
            primary = False
    return keys


def discover_chatgpt():
    with tempfile.TemporaryDirectory(prefix="debxiang-openai-") as temporary:
        root = Path(temporary)
        key = root / "key.gpg"
        download(KEY_URL, key)
        listing = run("gpg", "--homedir", root, "--batch", "--show-keys", "--with-colons", key,
                      capture_output=True, text=True).stdout
        if primary_fingerprints(listing) != [FINGERPRINT]:
            raise ValueError("OpenAI signing-key fingerprint changed; review the official signing key")
        run("gpg", "--homedir", root, "--batch", "--import", key, capture_output=True)
        inrelease = root / "InRelease"
        download(BASE + "dists/stable/InRelease", inrelease)
        release = root / "Release"
        run("gpg", "--homedir", root, "--batch", "--no-auto-key-retrieve", "--no-auto-key-import",
            "--output", release, "--decrypt", inrelease, capture_output=True)
        hashes = paragraphs(release.read_text())[0]["SHA256"]
        index_path = "main/binary-amd64/Packages"
        digest, size, _ = next(line.split() for line in hashes.splitlines()
                               if line.strip() and line.split()[-1] == index_path)
        index = root / "Packages"
        download(BASE + "dists/stable/" + index_path, index, digest)
        if index.stat().st_size != int(size):
            raise ValueError("OpenAI Packages size differs from the signed release")
        packages = [p for p in paragraphs(index.read_text())
                    if p.get("Package") == "chatgpt" and p.get("Architecture") == "amd64"]
        if len(packages) != 1:
            raise ValueError("Expected exactly one ChatGPT amd64 package in the official stable index")
        package = packages[0]
        filename = PurePosixPath(package["Filename"])
        if filename.is_absolute() or ".." in filename.parts or ":" in package["Filename"]:
            raise ValueError("Unexpected OpenAI package filename")
        run("dpkg", "--validate-version", package["Version"], capture_output=True)
        if not re.fullmatch(r"[a-fA-F0-9]{64}", package["SHA256"]):
            raise ValueError("Missing official package SHA256")
        return {"version": package["Version"], "deb_version": package["Version"],
                "url": urljoin(BASE, str(filename)), "sha256": package["SHA256"],
                "size": int(package["Size"]), "source_repository": BASE,
                "upstream_signing_fingerprint": FINGERPRINT}
