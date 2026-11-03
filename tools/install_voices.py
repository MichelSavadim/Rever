"""Baixa os modelos Piper uma vez; a geração posterior funciona offline."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path


MODELS = {
    "pt_BR-cadu-medium": "pt/pt_BR/cadu/medium",
    "pt_BR-faber-medium": "pt/pt_BR/faber/medium",
    "pt_BR-jeff-medium": "pt/pt_BR/jeff/medium",
    "pt_BR-edresson-low": "pt/pt_BR/edresson/low",
    "en_US-lessac-medium": "en/en_US/lessac/medium",
    "es_ES-davefx-medium": "es/es_ES/davefx/medium",
}
BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def download_voice(name: str, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for suffix in (".onnx.json", ".onnx"):
        target = directory / f"{name}{suffix}"
        if target.is_file() and target.stat().st_size > (100 if suffix.endswith("json") else 1_000_000):
            print(f"Já instalado: {target.name}")
            continue
        url = f"{BASE_URL}/{MODELS[name]}/{target.name}"
        temporary = target.with_name(target.name + ".download")
        print(f"Baixando {target.name}...", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if suffix.endswith("json"):
                json.loads(temporary.read_text(encoding="utf-8"))
            elif temporary.stat().st_size < 1_000_000:
                raise RuntimeError(f"Modelo incompleto: {target.name}")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Instala todas as vozes disponíveis")
    parser.add_argument("--pt-br-all", action="store_true", help="Instala apenas todas as vozes brasileiras")
    options = parser.parse_args()
    root = Path(__file__).resolve().parent.parent / "data" / "models"
    names = (MODELS if options.all else
             [name for name in MODELS if name.startswith("pt_BR-")]
             if options.pt_br_all else ("pt_BR-cadu-medium",))
    for name in names:
        download_voice(name, root)
    print(f"Vozes prontas em {root}. A geração não precisa de internet.")


if __name__ == "__main__":
    main()
