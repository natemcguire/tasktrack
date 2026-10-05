#!/usr/bin/env bash
# Local CI: everything the old GitHub "Verify Tasktrack" workflow ran.
# Runs from .githooks/pre-push and from scripts/ship.sh.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# Never let a suite reach the real Tasktrack from a developer shell.
unset TT_URL TT_TOKEN TT_ACTOR TT_SESSION

# Domain suite on the oldest and newest supported Pythons (old CI matrix).
for py in python3.11 python3.14; do
  command -v "$py" >/dev/null || { echo "verify: $py not found (brew install python@${py#python})"; exit 1; }
  echo "verify: domain suite on $($py --version)"
  "$py" -m unittest -q
  "$py" tests/mutation_checks.py
  "$py" tests/capture_api.py
done

# The browser suites rewrite checked-in evidence (docs/*.json, screenshots).
# If docs/ was clean going in, put it back so a push leaves no diff behind;
# run the npm scripts directly to refresh the evidence on purpose.
if git diff --quiet -- docs; then trap 'git checkout -q -- docs' EXIT; fi

[ -d node_modules ] || npm ci --silent
npx playwright install chromium >/dev/null
echo "verify: browser suite"
npm run --silent test:browser
echo "verify: hosted (local Cloudflare) suite"
npm run --silent test:hosted
echo "verify: all green"
