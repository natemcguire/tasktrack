#!/usr/bin/env bash
# Verify, push main, deploy to Cloudflare. Replaces the old GitHub
# test.yml -> deploy.yml chain. Wrangler auth comes from this machine
# (`wrangler whoami`) or CLOUDFLARE_API_TOKEN in the environment.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[ "$(git branch --show-current)" = main ] || { echo "ship: check out main first"; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "ship: uncommitted changes"; exit 1; }
git fetch -q origin main
git merge-base --is-ancestor origin/main HEAD || { echo "ship: behind origin/main, pull first"; exit 1; }

scripts/verify.sh
SKIP_VERIFY=1 git push origin main
npm run deploy:cloudflare
