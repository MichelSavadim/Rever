"""Registra exceções Python, mensagens Qt e falhas nativas em logs datados."""

from __future__ import annotations

import atexit
import faulthandler
import os
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

from .paths import data_root


LOG_PATH: Path | None = None
_stream: TextIO | None = None
_lock = threading.RLock()
_installed = False
_had_exception = False
_qt_handler = None


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write(message: str) -> None:
    with _lock:
        if _stream is not None:
            _stream.write(f"[{_stamp()}] {message}\n")
            _stream.flush()


def record(message: str) -> None:
    """Acrescenta um evento recuperável ao log, sem marcar a sessão como falha."""
    _write(message)


def log_exception(
    kind: type[BaseException], value: BaseException,
    tb: TracebackType | None, source: str = "Exceção não tratada",
) -> None:
    global _had_exception
    _had_exception = True
    with _lock:
        if _stream is None:
            if sys.__stderr__ is not None:
                traceback.print_exception(kind, value, tb, file=sys.__stderr__)
            return
        _stream.write(f"[{_stamp()}] {source}\n")
        traceback.print_exception(kind, value, tb, file=_stream)
        _stream.flush()
    if sys.__stderr__ is not None:
        traceback.print_exception(kind, value, tb, file=sys.__stderr__)


def _thread_exception(args: threading.ExceptHookArgs) -> None:
    name = args.thread.name if args.thread else "desconhecida"
    log_exception(args.exc_type, args.exc_value, args.exc_traceback, f"Exceção na thread {name}")


def _unraisable(args) -> None:
    log_exception(
        type(args.exc_value), args.exc_value, args.exc_traceback,
        f"Exceção ignorada em {args.object!r}",
    )


def _on_exit() -> None:
    _write("ENCERRAMENTO após erro" if _had_exception else "ENCERRAMENTO normal")
    if _stream is not None:
        _stream.flush()


def install(log_dir: Path | None = None) -> Path:
    """Instala captura antes de carregar Qt e devolve o caminho do crash log."""
    global LOG_PATH, _stream, _installed
    if _installed:
        assert LOG_PATH is not None
        return LOG_PATH
    directory = Path(
        log_dir or os.environ.get("PDF_READER_LOG_DIR")
        or data_root() / "logs"
    )
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    candidate = directory / f"crash_{stamp}.log"
    suffix = 2
    while candidate.exists():
        candidate = directory / f"crash_{stamp}_{suffix}.log"
        suffix += 1
    LOG_PATH = candidate
    _stream = LOG_PATH.open("x", encoding="utf-8", buffering=1)
    _installed = True
    _write("=" * 72)
    _write("INÍCIO do aplicativo")
    _write(f"Python: {sys.version.replace(chr(10), ' ')}")
    _write(f"Sistema: {platform.platform()}")
    _write(f"PID: {os.getpid()}")
    _write(f"Executável: {sys.executable}")
    _write(f"Diretório atual: {Path.cwd()}")
    _write(f"Argumentos: {sys.argv!r}")
    faulthandler.enable(file=_stream, all_threads=True)
    sys.excepthook = log_exception
    threading.excepthook = _thread_exception
    sys.unraisablehook = _unraisable
    atexit.register(_on_exit)
    return LOG_PATH


def install_qt_handler() -> None:
    """Registra avisos, erros e mensagens fatais do Qt no mesmo arquivo."""
    global _qt_handler
    import PySide6
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    _write(f"PySide6: {PySide6.__version__}")

    def handle_message(kind, context, message):
        label = getattr(kind, "name", str(kind))
        location = ""
        if context is not None and getattr(context, "file", None):
            location = f" ({context.file}:{context.line})"
        _write(f"Qt {label}{location}: {message}")
        if kind == QtMsgType.QtFatalMsg and _stream is not None:
            faulthandler.dump_traceback(file=_stream, all_threads=True)

    _qt_handler = handle_message  # impede que o callback Python seja coletado
    qInstallMessageHandler(_qt_handler)
