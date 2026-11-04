"""Vozes online do Edge TTS para reprodução e exportação em MP3."""

from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts


PREFIX = "edge_"
VOICES = {
    "Português (Brasil)": {
        "Francisca (Edge TTS · online)": "edge_pt-BR-FranciscaNeural",
        "Antonio (Edge TTS · online)": "edge_pt-BR-AntonioNeural",
    },
    "Inglês": {
        "Jenny (Edge TTS · online)": "edge_en-US-JennyNeural",
        "Guy (Edge TTS · online)": "edge_en-US-GuyNeural",
    },
    "Espanhol": {
        "Elvira (Edge TTS · online)": "edge_es-ES-ElviraNeural",
        "Alvaro (Edge TTS · online)": "edge_es-ES-AlvaroNeural",
    },
}
VOICE_IDS = {code for group in VOICES.values() for code in group.values()}


def is_edge_voice(voice: str) -> bool:
    return voice in VOICE_IDS


def synthesize_mp3(text: str, voice: str, target: Path) -> None:
    if not is_edge_voice(voice):
        raise ValueError(f"Voz Edge TTS desconhecida: {voice}")
    try:
        asyncio.run(edge_tts.Communicate(text, voice=voice.removeprefix(PREFIX)).save(str(target)))
    except Exception as exc:
        raise RuntimeError(f"Falha no Edge TTS. Verifique a conexão com a internet: {exc}") from exc
