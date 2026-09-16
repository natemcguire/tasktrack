"""Deploy with an optional local tenant configuration, without publishing it."""

import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = Path(
    os.environ.get(
        "TASKTRACK_CONFIG",
        "wrangler.private.json"
        if (root / "wrangler.private.json").exists()
        else "wrangler.jsonc",
    )
)
if not config.is_absolute():
    config = root / config
if config.parent.resolve() != root:
    raise SystemExit(
        "Keep deployment configuration in the project root so Wrangler includes the Python SDK."
    )
subprocess.run(
    [
        "npx",
        "wrangler",
        "d1",
        "migrations",
        "apply",
        "tasktrack-accounts",
        "--remote",
        "-c",
        str(config),
    ],
    cwd=root,
    check=True,
)
subprocess.run(
    ["uv", "run", "--group", "cloudflare", "pywrangler", "deploy", "-c", str(config)],
    cwd=root,
    check=True,
)
