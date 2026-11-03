"""Cliente para o Qwen3-TTS local, em um processo Python isolado."""

from __future__ import annotations

import atexit
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import imageio_ffmpeg

from .paths import data_root, resource_root


VOICES = {
    "Ryan (Qwen local)": "qwen3_ryan",
    "Serena (Qwen local)": "qwen3_serena",
}
SPEAKERS = {"qwen3_ryan": "Ryan", "qwen3_serena": "Serena"}
MODEL_DIR = "qwen3-tts-0.6b-customvoice"


def is_qwen_voice(voice: str) -> bool:
    return voice in SPEAKERS


def runtime_python() -> Path:
    return data_root() / ".venv-qwen" / "Scripts" / "python.exe"


def model_path() -> Path:
    return data_root() / "data" / "models" / MODEL_DIR


def installed_voices() -> dict[str, str]:
    if runtime_python().is_file() and (model_path() / "config.json").is_file():
        return VOICES.copy()
    return {}


class _QwenClient:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.messages: queue.Queue[dict] = queue.Queue()
        self.lock = threading.Lock()
        self.log = None

    def _read_messages(self, stream) -> None:
        for line in stream:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue

    def _wait(self, deadline: float, cancelled: Callable[[], bool]) -> dict | None:
        while time.monotonic() < deadline:
            if cancelled():
                self.stop()
                return None
            try:
                return self.messages.get(timeout=0.2)
            except queue.Empty:
                if self.process is None or self.process.poll() is not None:
                    raise RuntimeError("O processo do Qwen encerrou. Consulte logs/qwen.log.")
        self.stop()
        raise TimeoutError("O Qwen demorou demais para gerar o áudio. Consulte logs/qwen.log.")

    def start(self, cancelled: Callable[[], bool]) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        if self.process is not None:
            self.stop()
        python = runtime_python()
        model = model_path()
        if not python.is_file() or not (model / "config.json").is_file():
            raise FileNotFoundError("Qwen não instalado. Execute Instalar Qwen.cmd na pasta do Rever.")
        worker = resource_root() / "pdf_reader" / "qwen_worker.py"
        log_path = data_root() / "logs" / "qwen.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = log_path.open("a", encoding="utf-8")
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [str(python), "-u", str(worker), str(model)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read_messages, args=(self.process.stdout,), daemon=True).start()
        response = self._wait(time.monotonic() + 180, cancelled)
        if response is None:
            return False
        if response.get("status") != "ready":
            self.stop()
            raise RuntimeError(response.get("error", "Não foi possível carregar o modelo Qwen."))
        return True

    def generate(self, text: str, voice: str, wav_path: Path,
                 cancelled: Callable[[], bool]) -> bool:
        with self.lock:
            if not self.start(cancelled):
                return False
            assert self.process is not None and self.process.stdin is not None
            try:
                self.process.stdin.write(json.dumps({
                    "text": text, "speaker": SPEAKERS[voice], "output": str(wav_path),
                }) + "\n")
                self.process.stdin.flush()
            except OSError as exc:
                self.stop()
                raise RuntimeError("O processo do Qwen parou antes de receber o texto.") from exc
            response = self._wait(time.monotonic() + 300, cancelled)
            if response is None:
                return False
            if response.get("status") != "ok":
                raise RuntimeError(response.get("error", "Falha ao gerar áudio com Qwen."))
            return True

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
        if self.log is not None:
            self.log.close()
            self.log = None


_CLIENT = _QwenClient()
atexit.register(_CLIENT.stop)


def shutdown() -> None:
    _CLIENT.stop()


def synthesize_mp3(text: str, voice: str, target: Path,
                   cancelled: Callable[[], bool] = lambda: False) -> None:
    if voice not in SPEAKERS:
        raise ValueError(f"Voz Qwen desconhecida: {voice}")
    wav_path = target.with_suffix(".part.wav")
    try:
        if not _CLIENT.generate(text, voice, wav_path, cancelled):
            return
        if cancelled():
            return
        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "3", str(target)],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(f"Falha ao converter Qwen para MP3: {result.stderr.strip()}")
    finally:
        wav_path.unlink(missing_ok=True)
