"""Ponto de entrada que habilita diagnóstico antes de carregar a interface."""

from __future__ import annotations

import sys

from . import diagnostics


def main() -> int:
    log_path = diagnostics.install()
    try:
        from .app import main as app_main

        diagnostics.install_qt_handler()
        return app_main()
    except BaseException:
        kind, value, tb = sys.exc_info()
        assert kind is not None and value is not None
        diagnostics.log_exception(kind, value, tb, "Falha na inicialização ou execução")
        print(f"Relatório de falha: {log_path}", file=sys.__stderr__)
        return 1
