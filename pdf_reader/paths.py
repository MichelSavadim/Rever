"""Caminhos de dados graváveis e recursos incluídos no aplicativo."""

from __future__ import annotations

import sys
from pathlib import Path


def data_root() -> Path:
    """Pasta do projeto no Python ou pasta do executável empacotado."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Pasta de recursos incluídos pelo PyInstaller ou do código fonte."""
    return Path(__file__).resolve().parent.parent
