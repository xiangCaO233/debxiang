"""One serialized polling/build/signing transaction on a persistent runner."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
from build import deb_version
from common import run
from discover import discover
from repository import make_repository, prune_packages

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {"uv": ["uv"], "zig": ["zig", "zig-stable"], "ghostty": ["ghostty"]}


def maintain(state, site, engine):
    state.mkdir(parents=True, exist_ok=True)
    with (state / "maintain.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = discover()
        manifest_file = state / "manifest.json"
        previous = json.loads(manifest_file.read_text()) if manifest_file.exists() else {}
        pending = [name for name, data in plan.items()
                   if previous.get(name, {}).get("deb_version") != deb_version(data["version"])
                   or not all((state / "pool" / f"{pkg}_{deb_version(data['version'])}_amd64.deb").is_file()
                              for pkg in PACKAGES[name])]
        print("Pending components:", ", ".join(pending) or "none", flush=True)
        with tempfile.TemporaryDirectory(prefix="transaction-", dir=state) as temporary:
            transaction = Path(temporary)
            (transaction / "plan.json").write_text(json.dumps(plan, indent=2))
            output = transaction / "packages"
            output.mkdir()
            if pending:
                if shutil.disk_usage(state).free < 6 * 1024**3:
                    raise RuntimeError("At least 6 GiB free disk space required before building")
                run(engine, "build", "--pull", "-t", "localhost/debxiang-builder:trixie", ROOT)
                for component in pending:
                    args = [engine, "run", "--rm"]
                    if engine == "podman":
                        args += ["--userns=keep-id"]
                    else:
                        args += ["--user", f"{os.getuid()}:{os.getgid()}"]
                    cpu_count = max(1, int(run("nproc", capture_output=True, text=True).stdout) * 60 // 100)
                    args += ["--cpus", str(cpu_count), "-v", f"{ROOT}:/work:ro", "-v", f"{transaction}:/transaction",
                             "localhost/debxiang-builder:trixie", "python3", "/work/scripts/build.py",
                             component, "--plan", "/transaction/plan.json", "--output", "/transaction/packages"]
                    run(*args)
                    for package in PACKAGES[component]:
                        artifact = output / f"{package}_{deb_version(plan[component]['version'])}_amd64.deb"
                        if not artifact.is_file():
                            raise ValueError(f"Missing package: {artifact.name}")
                    # Install all outputs for this component in a clean base
                    # container and verify CLI use before committing the pool.
                    pattern = "zig" if component == "zig" else component
                    command = ("apt-get update && apt-get install -y /packages/" + pattern + "_*.deb " +
                               ("/packages/zig-stable_*.deb " if component == "zig" else "") +
                               "&& " + {"uv": "uv --version && uvx --version", "zig": "zig version && printf 'pub fn main() void {}' > /tmp/smoke.zig && zig build-exe /tmp/smoke.zig -femit-bin=/tmp/smoke && /tmp/smoke", "ghostty": "ghostty +version"}[component])
                    run(engine, "run", "--rm", "-v", f"{output}:/packages:ro", "debian:trixie",
                        "sh", "-ec", command)
            pool = transaction / "pool"
            if (state / "pool").exists():
                shutil.copytree(state / "pool", pool)
            else:
                pool.mkdir()
            for artifact in output.glob("*.deb"):
                shutil.copy2(artifact, pool / artifact.name)
            # Keep three recent versions for clients with older indexes.
            # Unlimited history belongs in Releases, not the Pages site.
            prune_packages(pool)
            if sum(p.stat().st_size for p in pool.glob("*.deb")) > 900 * 1024**2:
                raise RuntimeError("APT package pool exceeds the 900 MiB Pages budget")
            manifest = dict(previous)
            for component in pending:
                manifest[component] = dict(plan[component], deb_version=deb_version(plan[component]["version"]))
            if site.exists():
                shutil.rmtree(site)
            fingerprint = make_repository(pool, site, state, manifest)
            # Commit only after all packages and the signed index succeeded.
            (state / "pool").mkdir(exist_ok=True)
            for artifact in output.glob("*.deb"):
                shutil.copy2(artifact, state / "pool" / artifact.name)
            prune_packages(state / "pool")
            temporary_manifest = state / "manifest.json.tmp"
            temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
            temporary_manifest.replace(manifest_file)
            print("APT signing key:", fingerprint, flush=True)
            print("Repository prepared:", site, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--engine", choices=["podman", "docker"], default="podman")
    args = parser.parse_args()
    maintain(args.state.resolve(), args.site.resolve(), args.engine)
