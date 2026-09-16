"""Stage only application code for the Python Worker bundle."""

import hashlib
import shutil
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

assets = hashlib.sha256()
for asset in sorted((ROOT / "tasktrack/static").iterdir()):
    if asset.is_file():
        assets.update(asset.name.encode())
        assets.update(asset.read_bytes())
(destination / "tasktrack/asset_version.py").write_text(
    'ASSET_VERSION = "' + assets.hexdigest()[:16] + '"\n'
)
