"""Baixa os pesos oficiais do modelo para a biblioteca local do Rever."""

from __future__ import annotations

import sys

from huggingface_hub import snapshot_download


snapshot_download(
    repo_id="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    local_dir=sys.argv[1],
)
