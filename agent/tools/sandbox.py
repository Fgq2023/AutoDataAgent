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
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

_CHILD_TEMPLATE = dedent(
    """
    import sys
    import traceback

    import pandas as pd

    try:
        df = pd.read_csv(sys.argv[1])
        exec(compile(sys.argv[3], "<agent-code>", "exec"), globals())
        result.to_csv(sys.argv[2], index=False)
        print("sandbox: ok")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    """
)


class SandboxTimeout(Exception):
    """Raised when the child process exceeds its wall-clock budget."""


def run_isolated(code: str, input_csv: str | Path, output_csv: str | Path,
                 timeout_s: float) -> str:
    """Run *code* in a child interpreter; return its captured stdout.

    Raises :class:`SandboxTimeout` on timeout, :class:`RuntimeError` with
    the child's stderr on non-zero exit.
    """
    script = Path(tempfile.mkdtemp(prefix="ada_sandbox_")) / "_sandbox.py"
    script.write_text(_CHILD_TEMPLATE, encoding="utf-8")

    argv = [
        sys.executable,
        str(script),
        str(input_csv),
        str(output_csv),
        code,
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
