"""Isolated subprocess execution for generated code (subtopic C).

The sandbox runs a generated Python snippet in a *separate interpreter*:

  * the snippet receives the input as a DataFrame ``df``
  * whatever it binds to ``result`` (a DataFrame) is written to the output
  * stdout/stderr are captured; a wall-clock timeout kills the child

This is the v0.1 isolation layer (subprocess + temp dir); container-level
isolation (Docker) is planned for v0.2.
"""

from __future__ import annotations

import subprocess
import uuid
import shutil
import json
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

_CHILD_TEMPLATE = dedent(
    """
    import sys
    import traceback
    import json

    import pandas as pd

    try:
        meta = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
        df = pd.read_csv(sys.argv[1], converters={c: str for c in meta.get("string_columns", [])})
        for column, rows in meta.get("null_cells", {}).items():
            for row in rows:
                df.at[row, column] = None
        exec(compile(sys.argv[3], "<agent-code>", "exec"), globals())
        result.to_csv(sys.argv[2], index=False)
        strings = [str(c) for c in result.columns if str(result[c].dtype) in ("object", "str", "string")]
        metadata = {"string_columns": strings, "null_cells": {
            c: [i for i, missing in enumerate(result[c].isna()) if missing] for c in strings}}
        with open(sys.argv[2] + ".schema.json", "w", encoding="utf-8") as stream:
            json.dump(metadata, stream)
        print("sandbox: ok")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    """
)


class SandboxTimeout(Exception):
    """Raised when the child process exceeds its wall-clock budget."""


def run_isolated(code: str, input_csv: str | Path, output_csv: str | Path,
                 timeout_s: float, *, backend="subprocess",
                 image="autodataagent-sandbox:local", input_meta=None) -> str:
    """Run *code* in a child interpreter; return its captured stdout.

    Raises :class:`SandboxTimeout` on timeout, :class:`RuntimeError` with
    the child's stderr on non-zero exit.
    """
    if backend == "docker":
        return run_docker(code, input_csv, output_csv, timeout_s, image, input_meta)
    if backend != "subprocess":
        raise ValueError("unsupported sandbox backend")
    script = Path(tempfile.mkdtemp(prefix="ada_sandbox_")) / "_sandbox.py"
    script.write_text(_CHILD_TEMPLATE, encoding="utf-8")

    argv = [
        sys.executable,
        str(script),
        str(input_csv),
        str(output_csv),
        code,
        json.dumps(input_meta or {}),
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=str(script.parent),
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxTimeout(
            f"sandbox exceeded {timeout_s}s timeout: {exc}"
        ) from exc

    if proc.returncode != 0:
        raise RuntimeError(
            f"sandbox exited {proc.returncode}: {proc.stderr.strip()[:2000]}"
        )
    return proc.stdout



def run_docker(code, input_csv, output_csv, timeout_s, image, input_meta=None):
    """Mount only this input and a fresh output directory, without credentials."""
    name = "ada-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="ada_container_") as directory:
        root = Path(directory)
        inputs, outputs = root / "input", root / "output"
        inputs.mkdir()
        outputs.mkdir()
        shutil.copyfile(input_csv, inputs / "data.csv")
        (inputs / "main.py").write_text(_CHILD_TEMPLATE, encoding="utf-8")
        argv = [
            "docker", "run", "--rm", "--name", name, "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--memory=512m", "--cpus=1",
            "--pids-limit=64", "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
            "--mount", f"type=bind,source={inputs.resolve()},target=/input,readonly",
            "--mount", f"type=bind,source={outputs.resolve()},target=/output",
            image, "timeout", "--kill-after=1s", f"{max(0.1, timeout_s - 1)}s",
            "python", "-B", "/input/main.py", "/input/data.csv",
            "/output/result.csv", code, json.dumps(input_meta or {}),
        ]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
            raise SandboxTimeout(f"container exceeded {timeout_s}s") from exc
        except KeyboardInterrupt:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
            raise
        if proc.returncode == 124:
            raise SandboxTimeout("container-side deadline exceeded")
        if proc.returncode:
            raise RuntimeError(f"container failed: {proc.stderr[:2000]}")
        result = outputs / "result.csv"
        if not result.is_file() or result.is_symlink():
            raise RuntimeError("container produced no regular result.csv")
        Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(result, output_csv)
        schema = outputs / "result.csv.schema.json"
        if not schema.is_file() or schema.is_symlink():
            raise RuntimeError("container output schema is missing")
        shutil.copyfile(schema, str(output_csv) + ".schema.json")
        return proc.stdout
