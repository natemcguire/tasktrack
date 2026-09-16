"""Deploy with an optional local tenant configuration, without publishing it."""

import json
import os
import re
import shlex
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
# Tenant configurations may contain absolute paths. A copied config must never
# migrate or publish another checkout while the operator believes this one is used.
config_text = config.read_text()
inputs = [
    json.loads(m)
    for m in re.findall(
        r'"(?:main|directory|migrations_dir)"\s*:\s*("(?:\\.|[^"\\])*")', config_text
    )
]
for command in re.findall(r'"command"\s*:\s*("(?:\\.|[^"\\])*")', config_text):
    inputs.extend(
        part for part in shlex.split(json.loads(command)) if part.endswith(".py")
    )
for value in inputs:
    candidate = Path(value).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    if not candidate.resolve().is_relative_to(root):
        raise SystemExit(
            "Deployment configuration points outside this checkout. Update build, asset and migration paths before deploying."
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
