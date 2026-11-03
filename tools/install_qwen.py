"""Instala o Qwen3-TTS local em um ambiente separado do Rever."""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv-qwen" / "Scripts" / "python.exe"
MODEL = ROOT / "data" / "models" / "qwen3-tts-0.6b-customvoice"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    if not PYTHON.is_file():
        print("Criando ambiente isolado do Qwen…", flush=True)
        venv.EnvBuilder(with_pip=True).create(PYTHON.parent.parent)
    gpu_ready = subprocess.run(
        [str(PYTHON), "-c", "import torch; assert torch.cuda.is_available()"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if not gpu_ready:
        print("Instalando PyTorch com CUDA (download grande)…", flush=True)
        run(str(PYTHON), "-m", "pip", "install", "torch",
            "--index-url", "https://download.pytorch.org/whl/cu128")
    print("Instalando Qwen TTS…", flush=True)
    run(str(PYTHON), "-m", "pip", "install", "qwen-tts>=0.1,<1", "huggingface_hub>=0.30,<2")
    print("Baixando modelo gratuito para a pasta data/models…", flush=True)
    run(str(PYTHON), str(Path(__file__).with_name("download_qwen_model.py")), str(MODEL))
    print("Qwen TTS instalado. Reinicie o Rever para ver as vozes Ryan e Serena.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Falha na instalação (código {exc.returncode}).")
        raise SystemExit(exc.returncode)
