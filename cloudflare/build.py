"""Stage only application code for the Python Worker bundle."""

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
