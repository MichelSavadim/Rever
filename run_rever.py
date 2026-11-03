"""Entrada do aplicativo para execução pelo PyInstaller."""

import sys
import tempfile
from pathlib import Path

from pdf_reader.bootstrap import main


if __name__ == "__main__":
    if "--check-startup" in sys.argv:
        from pdf_reader import diagnostics

        diagnostics.install()
        try:
            from pdf_reader.app import MainWindow  # noqa: F401
        except BaseException:
            kind, value, tb = sys.exc_info()
            diagnostics.log_exception(kind, value, tb, "Falha no teste de inicialização do EXE")
            raise SystemExit(1)
        raise SystemExit(0)
    if "--check-qwen" in sys.argv:
        from pdf_reader.qwen_backend import VOICES, synthesize_mp3, shutdown

        try:
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "qwen-check.mp3"
                synthesize_mp3("Esta é uma amostra de leitura em português brasileiro.",
                               VOICES["Ryan (Qwen local)"], target)
                if not target.is_file() or target.stat().st_size == 0:
                    raise RuntimeError("O teste do Qwen não gerou MP3.")
        finally:
            shutdown()
        raise SystemExit(0)
    if "--check-voice" in sys.argv:
        from pdf_reader.local_tts import DEFAULT_VOICE, synthesize_mp3

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "voice-check.mp3"
            synthesize_mp3("Teste de voz local do Rever.", DEFAULT_VOICE, target)
            if target.stat().st_size == 0:
                raise RuntimeError("O teste da voz gerou um MP3 vazio.")
        raise SystemExit(0)
    raise SystemExit(main())
