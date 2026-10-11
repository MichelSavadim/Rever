"""Gera o executável Windows de arquivo único com PyInstaller."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    environment = os.environ.copy()
    # Ferramentas de desenvolvimento podem colocar DLLs incompatíveis no PATH.
    environment["PATH"] = os.pathsep.join(
        entry for entry in environment.get("PATH", "").split(os.pathsep)
        if "codex-runtimes" not in entry.lower()
    )
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", "Rever",
        "--icon", "pdf_reader/assets/icons/rever.ico",
        "--add-data", "README.md:.",
        "--add-data", "pdf_reader/assets/icons:pdf_reader/assets/icons",
        "--collect-all", "imageio_ffmpeg",
        "--workpath", "build", "--distpath", ".", "run_rever.py",
    ]
    return subprocess.call(command, cwd=root, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
