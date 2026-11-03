"""Gera o executável Windows de arquivo único com PyInstaller."""

from __future__ import annotations

import os
import argparse
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Rever")
    args = parser.parse_args()
    if not re.fullmatch(r"Rever(?:-\d+\.\d+\.\d+(?:-corrigido)?)?", args.name):
        parser.error("O nome deve ser Rever, Rever-X.Y.Z ou Rever-X.Y.Z-corrigido")
    root = Path(__file__).resolve().parent
    environment = os.environ.copy()
    # Ferramentas de desenvolvimento podem colocar DLLs incompatíveis no PATH.
    environment["PATH"] = os.pathsep.join(
        entry for entry in environment.get("PATH", "").split(os.pathsep)
        if "codex-runtimes" not in entry.lower()
    )
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", args.name,
        "--icon", "pdf_reader/assets/icons/rever.ico",
        "--add-data", "README.md:.",
        "--add-data", "PIPER_LICENSE.txt:.",
        "--add-data", "pdf_reader/assets/icons:pdf_reader/assets/icons",
        "--add-data", "pdf_reader/qwen_worker.py:pdf_reader",
        "--collect-all", "imageio_ffmpeg",
        "--collect-all", "piper",
        "--workpath", str(Path("build") / args.name), "--distpath", ".", "run_rever.py",
    ]
    models = root / "data" / "models"
    if not (models / "pt_BR-cadu-medium.onnx").is_file():
        print("Instale a voz antes da build: .\\.venv\\Scripts\\python.exe tools\\install_voices.py")
        return 1
    piper_files = sorted(models.glob("*.onnx")) + sorted(models.glob("*.onnx.json"))
    bundled_models = [part for path in piper_files
                      for part in ("--add-data", f"{path.relative_to(root).as_posix()}:data/models")]
    command[command.index("--workpath"):command.index("--workpath")] = bundled_models
    result = subprocess.call(command, cwd=root, env=environment)
    if result:
        return result
    try:
        check = subprocess.run(
            [str(root / f"{args.name}.exe"), "--check-startup"],
            cwd=root, env=environment, timeout=120, check=False,
        )
    except subprocess.TimeoutExpired:
        print("O teste de inicialização do EXE excedeu 120 segundos. Consulte logs/.")
        return 1
    if check.returncode:
        print("O EXE não passou no teste de inicialização. Consulte o crashlog mais recente.")
    return check.returncode


if __name__ == "__main__":
    raise SystemExit(main())
