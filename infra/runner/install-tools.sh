#!/usr/bin/env bash
# Run on the provisioned Ubuntu x86_64 host as root; no provider authentication.
set -euo pipefail
[[ $EUID -eq 0 && $(uname -m) == x86_64 ]] || exit 64
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
curl --fail --silent --show-error --location https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt -o SHASUMS256.txt
artifact=$(awk '$2 ~ /^node-v24\.[0-9]+\.[0-9]+-linux-x64\.tar\.xz$/ {print $2}' SHASUMS256.txt)
[[ -n "$artifact" && "$artifact" != *$'\n'* ]] || exit 65
curl --fail --silent --show-error --location "https://nodejs.org/dist/latest-v24.x/$artifact" -o "$artifact"
awk -v name="$artifact" '$2 == name' SHASUMS256.txt | sha256sum --check -
tar --extract --xz --file "$artifact" --directory /usr/local --strip-components=1 --no-same-owner
node --version
npm --version
# Official standalone installers run as the unprivileged account. Download first;
# retain installer hashes for the private provisioning record.
install -d -m 0700 -o runner -g runner /home/runner/.local /home/runner/.local/bin /home/runner/.local/installers
runuser -u runner -- env HOME=/home/runner bash -c '
set -euo pipefail
cd /home/runner/.local/installers
curl -fsSL https://chatgpt.com/codex/install.sh -o codex-install.sh
curl -fsSL https://claude.ai/install.sh -o claude-install.sh
sha256sum codex-install.sh claude-install.sh > installer-sha256.txt
sh codex-install.sh
bash claude-install.sh stable
'
runuser -u runner -- env HOME=/home/runner PATH=/home/runner/.local/bin:/usr/local/bin:/usr/bin:/bin bash -c 'codex --version; claude --version'
