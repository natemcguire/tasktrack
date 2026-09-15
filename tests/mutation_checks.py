"""Prove selected regression tests reject deliberately broken implementations.

Each mutant runs in a temporary source copy; the working checkout is untouched.
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTANTS = [
    ("claim guard disabled", "claim", "test_claim_cannot_overwrite_at_current_version"),
    ("project rename skipped", "rename", "test_rename_aliases_and_noops"),
    (
        "idempotent creation duplicated",
        "retry",
        "test_idempotent_creation_and_version_retry",
    ),
]


def mutate(source, kind):
    tree = ast.parse(source)
    changed = 0
    for node in ast.walk(tree):
        if (
            kind == "claim"
            and isinstance(node, ast.If)
            and isinstance(node.body[0], ast.Raise)
        ):
            if any(
                isinstance(x, ast.Constant)
                and isinstance(x.value, str)
                and x.value.startswith("This task is already claimed.")
                for x in ast.walk(node.body[0])
            ):
                node.test = ast.Constant(False)
                changed += 1
        if (
            kind == "retry"
            and isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "receipt"
        ):
            node.test = ast.Constant(False)
            changed += 1
        if (
            not isinstance(node, ast.Call)
            or not node.args
            or not isinstance(node.args[0], ast.Constant)
            or not isinstance(node.args[0].value, str)
        ):
            continue
        sql = node.args[0].value
        if kind == "rename" and sql.startswith("UPDATE projects SET"):
            node.args[1].elts[0] = ast.parse('previous["key"]', mode="eval").body
            node.args[1].elts[1] = ast.parse('previous["name"]', mode="eval").body
            changed += 1
        if kind == "retry" and sql.startswith("INSERT INTO receipts VALUES"):
            node.args[0].value = sql.replace("INSERT INTO", "INSERT OR REPLACE INTO", 1)
            changed += 1
    if changed != (2 if kind == "retry" else 1):
        raise RuntimeError(
            f"Expected mutation sites changed for {kind}; update this check."
        )
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def main():
    for name, kind, test in MUTANTS:
        with tempfile.TemporaryDirectory(prefix="tasktrack-mutant-") as temporary:
            directory = Path(temporary)
            for folder in ("tasktrack", "tests"):
                shutil.copytree(
                    ROOT / folder,
                    directory / folder,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
            file = directory / "tasktrack/service.py"
            file.write_text(mutate(file.read_text(), kind))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    f"tests.test_tasktrack.DomainTests.{test}",
                ],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(directory)},
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0 or "FAIL:" not in result.stderr:
                print(result.stdout, result.stderr)
                raise RuntimeError(
                    f"Mutant survived or crashed outside assertions: {name}"
                )
            print(f"CAUGHT: {name} → {test}")


if __name__ == "__main__":
    main()
