#!/usr/bin/env bash
# Install a second independent runner; registration token stays with the user.
set -euo pipefail
runner_dir="${HOME}/actions-runner-debxiang"
for tool in python3 podman gpg apt-ftparchive gh curl systemctl; do
    command -v "$tool" >/dev/null || { printf 'Missing tool: %s\n' "$tool" >&2; exit 1; }
done
if [[ -e "$runner_dir/.runner" ]]; then
    printf 'Runner already registered: %s\n' "$runner_dir"
    exit 1
fi
mkdir -p "$runner_dir" "${HOME}/.config/systemd/user" "${HOME}/.local/state/debxiang"
if [[ ! -f "$runner_dir/run.sh" ]]; then
    python3 - "$runner_dir" <<'PY'
import hashlib, json, pathlib, tarfile, urllib.request, sys
root = pathlib.Path(sys.argv[1])
req = urllib.request.Request('https://api.github.com/repos/actions/runner/releases/latest', headers={'User-Agent': 'debxiang'})
with urllib.request.urlopen(req, timeout=60) as response:
    release = json.load(response)
asset = next(a for a in release['assets'] if a['name'].startswith('actions-runner-linux-x64-') and a['name'].endswith('.tar.gz'))
digest = asset.get('digest') or ''
if not digest.startswith('sha256:'):
    raise SystemExit('Runner release has no SHA256 asset digest; verify manually before installing')
archive = root / 'runner.tar.gz'
with urllib.request.urlopen(asset['browser_download_url'], timeout=120) as response, archive.open('wb') as output:
    while chunk := response.read(1024*1024):
        output.write(chunk)
with archive.open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
if actual != digest.removeprefix('sha256:'):
    raise SystemExit('Runner checksum mismatch')
with tarfile.open(archive) as tar:
    tar.extractall(root, filter='data')
archive.unlink()
print('Runner downloaded and verified:', release['tag_name'])
PY
fi
cat > "${HOME}/.config/systemd/user/debxiang-runner.service" <<EOF
[Unit]
Description=GitHub Actions runner for debxiang
After=network-online.target

[Service]
WorkingDirectory=${runner_dir}
ExecStart=${runner_dir}/run.sh
Restart=on-failure
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=300

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
printf 'Ready. Register in %s with --labels debxiang; then enable debxiang-runner.service.\n' "$runner_dir"
