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
        "--add-data", "PIPER_LICENSE.txt:.",
        "--add-data", "pdf_reader/assets/icons:pdf_reader/assets/icons",
        "--add-data", "pdf_reader/qwen_worker.py:pdf_reader",
        "--collect-all", "imageio_ffmpeg",
        "--collect-all", "piper",
        "--workpath", "build", "--distpath", ".", "run_rever.py",
    ]
    models = root / "data" / "models"
    if not (models / "pt_BR-cadu-medium.onnx").is_file():
        print("Instale a voz antes da build: .\\.venv\\Scripts\\python.exe tools\\install_voices.py")
        return 1
    piper_files = sorted(models.glob("*.onnx")) + sorted(models.glob("*.onnx.json"))
    bundled_models = [part for path in piper_files
                      for part in ("--add-data", f"{path.relative_to(root).as_posix()}:data/models")]
    command[command.index("--workpath"):command.index("--workpath")] = bundled_models
    return subprocess.call(command, cwd=root, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
