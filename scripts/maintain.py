"""One serialized polling/build/signing transaction on a persistent runner."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
from build import deb_version, package_version
from common import run
from discover import DISCOVERERS
from repository import make_repository, prune_packages

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {"uv": ["uv"], "zig": ["zig", "zig-stable"], "ghostty": ["ghostty"], "chatgpt": ["chatgpt"]}


def maintain(state, site, engine):
    state.mkdir(parents=True, exist_ok=True)
    with (state / "maintain.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan, failures = {}, []
        for name, fetch in DISCOVERERS.items():
            try:
                plan[name] = fetch()
            except Exception as error:
                failures.append(f"{name} discovery: {error}")
                print(failures[-1], flush=True)
        completed = []
        manifest_file = state / "manifest.json"
        previous = json.loads(manifest_file.read_text()) if manifest_file.exists() else {}
        for name, data in list(plan.items()):
            old = previous.get(name, {})
            if (old.get("deb_version") == package_version(data, name) and old.get("sha256")
                    and data.get("sha256") and old["sha256"] != data["sha256"]):
                failures.append(f"{name}: upstream changed the checksum without changing the version")
                print(failures[-1], flush=True)
                del plan[name]
        pending = [name for name, data in plan.items()
                   if previous.get(name, {}).get("deb_version") != package_version(data, name)
                   or not all((state / "pool" / f"{pkg}_{package_version(data, name)}_amd64.deb").is_file()
                              for pkg in PACKAGES[name])]
        print("Pending components:", ", ".join(pending) or "none", flush=True)
        deployed = state / "deployed-manifest.sha256"
        if (os.environ.get("GITHUB_OUTPUT") and not pending and not failures
                and manifest_file.exists() and deployed.exists()
                and hashlib.sha256(manifest_file.read_bytes()).hexdigest() == deployed.read_text().strip()):
            with open(os.environ["GITHUB_OUTPUT"], "a") as output_file:
                output_file.write("failures_count=0\nchanged=false\n")
            print("No upstream changes since the last successful deployment", flush=True)
            return []
        with tempfile.TemporaryDirectory(prefix="transaction-", dir=state) as temporary:
            transaction = Path(temporary)
            (transaction / "plan.json").write_text(json.dumps(plan, indent=2))
            output = transaction / "packages"
            output.mkdir()
            if pending:
                if shutil.disk_usage(state).free < 6 * 1024**3:
                    raise RuntimeError("At least 6 GiB free disk space required before building")
                run(engine, "build", "--pull", "-t", "localhost/debxiang-builder:trixie", ROOT)
                downloads = state / "downloads"
                downloads.mkdir(exist_ok=True)
                for component in pending:
                    try:
                        args = [engine, "run", "--rm"]
                        if engine == "podman":
                            args += ["--userns=keep-id"]
                        else:
                            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
                        cpu_count = max(1, int(run("nproc", capture_output=True, text=True).stdout) * 60 // 100)
                        args += ["--cpus", str(cpu_count), "-v", f"{ROOT}:/work:ro", "-v", f"{transaction}:/transaction",
                                 "-v", f"{downloads}:/downloads", "-e", "DEBXIANG_DOWNLOAD_CACHE=/downloads",
                                 "localhost/debxiang-builder:trixie", "python3", "/work/scripts/build.py",
                                 component, "--plan", "/transaction/plan.json", "--output", "/transaction/packages"]
                        run(*args)
                        for package in PACKAGES[component]:
                            artifact = output / f"{package}_{package_version(plan[component], component)}_amd64.deb"
                            if not artifact.is_file():
                                raise ValueError(f"Missing package: {artifact.name}")
                        # Install all outputs for this component in a clean base
                        # container and verify CLI use before committing the pool.
                        pattern = "zig" if component == "zig" else component
                        command = ("apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y /packages/" + pattern + "_*.deb " +
                                   ("/packages/zig-stable_*.deb " if component == "zig" else "") +
                                   "&& " + {"uv": "uv --version && uvx --version", "zig": "zig version && printf 'pub fn main() void {}' > /tmp/smoke.zig && zig build-exe /tmp/smoke.zig -femit-bin=/tmp/smoke && /tmp/smoke", "ghostty": "ghostty +version", "chatgpt": "dpkg-query -W chatgpt && test -x /usr/bin/chatgpt"}[component])
                        if component == "ghostty":
                            # Cover both our target release and newer Debian
                            # systems where ncurses-term already owns the alias.
                            for suite in ("trixie", "sid"):
                                run(engine, "run", "--rm", "-v", f"{output}:/packages:ro",
                                    "-v", f"{ROOT}:/work:ro", "-v", f"{state / 'pool'}:/previous:ro",
                                    "debian:" + suite,
                                    "sh", "/work/scripts/check-ghostty.sh")
                        else:
                            run(engine, "run", "--rm", "-v", f"{output}:/packages:ro", "debian:trixie",
                                "sh", "-ec", command)
                        completed.append(component)
                    except Exception as error:
                        failures.append(f"{component} build/install: {error}")
                        print(failures[-1], flush=True)
                        for package in PACKAGES[component]:
                            for artifact in output.glob(package + "_*.deb"):
                                artifact.unlink()
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
            if not list(pool.glob("*.deb")):
                raise RuntimeError("No verified packages available: " + "; ".join(failures))
            manifest = dict(previous)
            for component in completed:
                manifest[component] = dict(plan[component], deb_version=package_version(plan[component], component))
            if site.exists():
                shutil.rmtree(site)
            fingerprint = make_repository(pool, site, state, manifest)
            # Commit verified components only after the signed index succeeds.
            (state / "pool").mkdir(exist_ok=True)
            for artifact in output.glob("*.deb"):
                shutil.copy2(artifact, state / "pool" / artifact.name)
            prune_packages(state / "pool")
            temporary_manifest = state / "manifest.json.tmp"
            temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
            temporary_manifest.replace(manifest_file)
            (site / "failures.json").write_text(json.dumps(failures, indent=2) + "\n")
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as output_file:
                    output_file.write(f"failures_count={len(failures)}\n")
                    output_file.write("changed=true\n")
                    output_file.write("manifest_sha256=" + hashlib.sha256(manifest_file.read_bytes()).hexdigest() + "\n")
            print("APT signing key:", fingerprint, flush=True)
            print("Repository prepared:", site, flush=True)
            return failures


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Runner interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--engine", choices=["podman", "docker"], default="podman")
    args = parser.parse_args()
    failures = maintain(args.state.resolve(), args.site.resolve(), args.engine)
    if failures and not os.environ.get("GITHUB_OUTPUT"):
        raise SystemExit(1)
