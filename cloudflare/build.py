"""Stage only application code for the Python Worker bundle."""

import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
destination = ROOT / "build/cloudflare"
destination.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / "worker.py", destination / "worker.py")
shutil.copytree(
    ROOT / "tasktrack",
    destination / "tasktrack",
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("__pycache__", "static", "vendor", "*.pyc"),
)


# The mature WebAuthn verifier is bundled as a private Worker ES module, never a browser asset.
subprocess.run(
    [
        str(ROOT / "node_modules/.bin/esbuild"),
        str(ROOT / "cloudflare/provider-crypto.js"),
        "--bundle",
        "--format=esm",
        "--platform=browser",
        "--outfile=" + str(destination / "provider-crypto.mjs"),
    ],
    check=True,
)
subprocess.run(
    [
        str(ROOT / "node_modules/.bin/esbuild"),
        str(ROOT / "cloudflare/passkey-server.js"),
        "--bundle",
        "--format=esm",
        "--platform=browser",
        "--outfile=" + str(destination / "passkey-server.mjs"),
    ],
    check=True,
)
subprocess.run(
    [
        str(ROOT / "node_modules/.bin/esbuild"),
        str(ROOT / "cloudflare/passkey-browser.js"),
        "--bundle",
        "--format=iife",
        "--minify",
        "--outfile=" + str(destination / "passkeys-browser.js"),
    ],
    check=True,
)

# Avoid triggering our own tasktrack/ watcher on every build.
browser_bundle = (destination / "passkeys-browser.js").read_bytes()
browser_asset = ROOT / "tasktrack/static/passkeys.js"
if not browser_asset.exists() or browser_asset.read_bytes() != browser_bundle:
    browser_asset.write_bytes(browser_bundle)

assets = hashlib.sha256()
for asset in sorted((ROOT / "tasktrack/static").iterdir()):
    if asset.is_file():
        assets.update(asset.name.encode())
        assets.update(asset.read_bytes())
(destination / "tasktrack/asset_version.py").write_text(
    'ASSET_VERSION = "' + assets.hexdigest()[:16] + '"\n'
)
