"""Síntese local com Piper e conversão dos trechos para MP3."""

from __future__ import annotations

import subprocess
import wave
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg

from .paths import data_root, resource_root


DEFAULT_VOICE = "pt_BR-cadu-medium"
VOICES = {
    "Português (Brasil)": {
        "Cadu (média)": DEFAULT_VOICE,
        "Faber (média)": "pt_BR-faber-medium",
        "Jeff (média)": "pt_BR-jeff-medium",
        "Edresson (baixa)": "pt_BR-edresson-low",
    },
    "English (US)": {"Lessac": "en_US-lessac-medium"},
    "Español (España)": {"Dave": "es_ES-davefx-medium"},
}


def installed_voices() -> dict[str, dict[str, str]]:
    available: dict[str, dict[str, str]] = {}
    for language, voices in VOICES.items():
        for name, code in voices.items():
            try:
                model_path(code)
            except FileNotFoundError:
                continue
            available.setdefault(language, {})[name] = code
    return available or {"Português (Brasil)": {"Cadu (média)": DEFAULT_VOICE}}


def model_path(voice: str) -> Path:
    if voice not in {code for group in VOICES.values() for code in group.values()}:
        raise ValueError(f"Voz Piper desconhecida: {voice}")
    for root in (data_root(), resource_root()):
        path = root / "data" / "models" / f"{voice}.onnx"
        if path.is_file() and path.with_suffix(".onnx.json").is_file():
            return path
    raise FileNotFoundError(
        f"Modelo da voz {voice} não encontrado. Execute "
        ".\\.venv\\Scripts\\python.exe tools\\install_voices.py "
        "na pasta do Rever e tente novamente."
    )


@lru_cache(maxsize=4)
def _load_voice(path: Path):
    from piper import PiperVoice

    return PiperVoice.load(str(path))


def synthesize_mp3(text: str, voice: str, target: Path) -> None:
    """Gera um MP3 temporário completo, sem solicitar nenhum serviço remoto."""
    path = model_path(voice)
    wav_path = target.with_suffix(".part.wav")
    try:
        with wave.open(str(wav_path), "wb") as wav_file:
            _load_voice(path).synthesize_wav(text, wav_file)
        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "3", str(target)],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(f"Falha ao converter áudio Piper para MP3: {result.stderr.strip()}")
    finally:
        wav_path.unlink(missing_ok=True)
