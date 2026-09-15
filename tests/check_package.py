"""Verify the built wheel installs and runs outside the checkout, without Node."""

import json
import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    wheels = list((ROOT / "dist").glob("tasktrack-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Build exactly one current wheel with uv build first.")
    with tempfile.TemporaryDirectory(prefix="tasktrack-package-") as temporary:
        folder = Path(temporary)
        subprocess.run(
            [sys.executable, "-m", "venv", str(folder / "venv")],
            check=True,
            capture_output=True,
        )
        python = folder / "venv/bin/python"
        executable = folder / "venv/bin/tt"
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])],
            check=True,
            capture_output=True,
        )
        env = {**os.environ, "TT_DATA_DIR": str(folder / "data")}
        for key in ("PYTHONPATH", "TT_ACTOR", "TT_SESSION"):
            env.pop(key, None)
        result = subprocess.run(
            [
                str(executable),
                "project",
                "create",
                "PKG",
                "Installed package",
                "--as",
                "package-test",
                "--json",
            ],
            cwd=folder,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        project = json.loads(result.stdout)
        assert project["key"] == "PKG"
        process = subprocess.Popen(
            [str(executable), "serve", "--port", "0"],
            cwd=folder,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            if not select.select([process.stdout], [], [], 10)[0]:
                raise RuntimeError("Installed server did not start.")
            url = process.stdout.readline().split("listening on ")[1].split(" ")[0]
            with urlopen(url + f"/api/v1/projects/{project['id']}") as response:
                assert json.loads(response.read())["name"] == "Installed package"
            for endpoint, asset in [
                ("/", "index.html"),
                ("/app.js", "app.js"),
                ("/style.css", "style.css"),
            ]:
                with urlopen(url + endpoint) as response:
                    assert (
                        response.read()
                        == (ROOT / "tasktrack/static" / asset).read_bytes()
                    )
            print(
                "PASS: wheel installs in an isolated venv; installed tt writes persist through HTTP; all offline assets match."
            )
        finally:
            process.terminate()
            _, error = process.communicate(timeout=10)
            if error:
                raise RuntimeError(error)


if __name__ == "__main__":
    main()
