"""Build Debian trixie/amd64 packages inside the dedicated container."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from common import download, extract, get_json, request, run, version

GHOSTTY_KEY = "RWQlAjJC23149WL2sEpT/l0QKy7hMIFhYdQOFy0Z7z7PbneUgvlsnYcV"


def deb_version(upstream):
    # A new packaging revision must exceed Griffo's installed version too.
    return version(upstream) + "-100~debxiang1~trixie"


def control(root, name, upstream, depends="", description=""):
    directory = root / "DEBIAN"
    directory.mkdir(exist_ok=True)
    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and "DEBIAN" not in p.parts)
    fields = [f"Package: {name}", f"Version: {deb_version(upstream)}", "Architecture: amd64",
              "Maintainer: debxiang maintainers <noreply@github.com>", "Section: devel",
              "Priority: optional", f"Installed-Size: {(size + 1023) // 1024}"]
    if depends:
        fields.append("Depends: " + depends)
    fields.append("Description: " + description)
    (directory / "control").write_text("\n".join(fields) + "\n")


def package(root, name, upstream, output):
    target = output / f"{name}_{deb_version(upstream)}_amd64.deb"
    run("dpkg-deb", "--root-owner-group", "--build", root, target)
    return target


def runtime_dependencies(root, binary):
    # dpkg-shlibdeps needs a minimal Debian source context.
    (root / "debian").mkdir()
    (root / "debian/control").write_text("Source: debxiang\n\nPackage: debxiang\nArchitecture: any\n")
    result = run("dpkg-shlibdeps", "-O", "-e" + str(binary), cwd=root, capture_output=True, text=True)
    shutil.rmtree(root / "debian")
    return next(line.split("=", 1)[1] for line in result.stdout.splitlines() if line.startswith("shlibs:Depends="))


def required_zig(text, zon=""):
    match = re.search(r'\.minimum_zig_version\s*=\s*"(\d+\.\d+\.\d+)"', zon)
    if match:
        return version(match[1])
    match = re.search(r'required_zig\s*=\s*(?:[^;]*?)"(\d+\.\d+\.\d+)"', text)
    if match:
        return version(match[1])
    # Upstream also expresses the version as a SemanticVersion struct.
    match = re.search(r'required_zig[^=]*=\s*[^\{]*\{([^}]+)\}', text)
    if match:
        parts = [re.search(r'\.' + key + r'\s*=\s*(\d+)', match[1]) for key in ("major", "minor", "patch")]
        if all(parts):
            return ".".join(m[1] for m in parts)
    raise ValueError("Cannot determine Ghostty's required_zig; inspect this release's build.zig")


def add_copyright(root, name, source, upstream_url):
    directory = root / "usr/share/doc" / name
    directory.mkdir(parents=True, exist_ok=True)
    candidates = [source / x for x in ("LICENSE", "LICENSE.txt", "LICENSE-MIT")]
    licenses = [p for p in candidates if p.is_file()]
    if not licenses:
        raise ValueError(f"Missing upstream license for {name}")
    (directory / "copyright").write_text("Upstream: " + upstream_url + "\n\n" +
                                        "\n\n".join(p.read_text() for p in licenses))


def build(component, metadata, output):
    upstream = version(metadata["version"])
    with tempfile.TemporaryDirectory(prefix="debxiang-") as temporary:
        work = Path(temporary)
        archive = work / ("upstream.tar.xz" if component == "zig" else "upstream.tar.gz")
        checksum = metadata.get("sha256")
        if not checksum and metadata.get("checksum_url"):
            with request(metadata["checksum_url"]) as response:
                checksum = response.read().decode().split()[0]
            if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
                raise ValueError("Invalid upstream SHA256 file")
        download(metadata["url"], archive, checksum)
        if component == "ghostty":
            download(metadata["url"] + ".minisig", work / "source.minisig")
            run("minisign", "-Vm", archive, "-x", work / "source.minisig", "-P", GHOSTTY_KEY)
        source = extract(archive, work / "source")
        root = work / "root"
        (root / "usr/bin").mkdir(parents=True)
        if component == "uv":
            for binary in ("uv", "uvx"):
                shutil.copy2(source / binary, root / "usr/bin" / binary)
            # Official binary archive does not necessarily include licenses.
            for license_name in ("LICENSE-MIT", "LICENSE-APACHE"):
                download(f"https://raw.githubusercontent.com/astral-sh/uv/{upstream}/{license_name}", source / license_name)
            add_copyright(root, "uv", source, "https://github.com/astral-sh/uv")
            apache = root / "usr/share/doc/uv/LICENSE-APACHE"
            shutil.copy2(source / "LICENSE-APACHE", apache)
            dependency = runtime_dependencies(root, root / "usr/bin/uv")
            # Include uvx as well, even if upstream changes its dependencies.
            dependency2 = runtime_dependencies(root, root / "usr/bin/uvx")
            dependency = ", ".join(dict.fromkeys((dependency + ", " + dependency2).split(", ")))
            run(root / "usr/bin/uv", "--version")
            control(root, "uv", upstream, dependency, "Fast Python package and project manager")
            package(root, "uv", upstream, output)
        elif component == "zig":
            zig_home = root / "usr/lib/zig" / upstream
            shutil.copytree(source, zig_home)
            # Match Griffo's alternatives mechanism so zig-oldstable can
            # coexist, and upgrading removes the old provider correctly.
            scripts = root / "DEBIAN"
            scripts.mkdir()
            zig_path = f"/usr/lib/zig/{upstream}/zig"
            (scripts / "postinst").write_text(
                '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n'
                f'    update-alternatives --install /usr/bin/zig zig {zig_path} 100\nfi\n')
            (scripts / "prerm").write_text(
                '#!/bin/sh\nset -e\ncase "$1" in remove|upgrade|deconfigure)\n'
                f'    update-alternatives --remove zig {zig_path}\n;; esac\n')
            for script in ("postinst", "prerm"):
                (scripts / script).chmod(0o755)
            add_copyright(root, "zig-stable", source, "https://ziglang.org")
            run(zig_home / "zig", "version")
            control(root, "zig-stable", upstream, "", "Zig stable compiler and standard library")
            package(root, "zig-stable", upstream, output)
            meta = work / "meta"
            meta.mkdir()
            control(meta, "zig", upstream, f"zig-stable (= {deb_version(upstream)})", "Metapackage for the stable Zig compiler")
            package(meta, "zig", upstream, output)
        elif component == "ghostty":
            zon = (source / "build.zig.zon").read_text()
            zig_version = required_zig((source / "build.zig").read_text(), zon)
            source_version = re.search(r'\.version\s*=\s*"([^"\n]+)"', zon)
            if source_version and source_version[1] != upstream:
                raise ValueError("Ghostty source version does not match the selected tag")
            zig = get_json("https://ziglang.org/download/index.json")[zig_version]["x86_64-linux"]
            download(zig["tarball"], work / "zig.tar.xz", zig["shasum"])
            compiler = extract(work / "zig.tar.xz", work / "compiler") / "zig"
            jobs = max(1, int(run("nproc", capture_output=True, text=True).stdout) * 60 // 100)
            environment = dict(os.environ, DESTDIR=str(root), ZIG_GLOBAL_CACHE_DIR=str(work / "zig-cache"),
                               PATH=str(compiler.parent) + os.pathsep + os.environ["PATH"])
            run("bash", source / "nix/build-support/fetch-zig-cache.sh", cwd=source, env=environment)
            run(compiler, "build", "--prefix", "/usr", "--system", work / "zig-cache/p",
                "-Doptimize=ReleaseFast", "-Dcpu=baseline",
                "-j" + str(jobs), cwd=source, env=environment)
            binary = root / "usr/bin/ghostty"
            if not binary.is_file():
                raise ValueError("Ghostty install did not produce /usr/bin/ghostty")
            dependency = runtime_dependencies(root, binary)
            add_copyright(root, "ghostty", source, "https://github.com/ghostty-org/ghostty")
            run(binary, "+version")
            control(root, "ghostty", upstream, dependency, "GPU accelerated terminal emulator")
            package(root, "ghostty", upstream, output)
        else:
            raise ValueError(component)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["uv", "zig", "ghostty"])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    build(args.component, json.loads(args.plan.read_text())[args.component], args.output)
