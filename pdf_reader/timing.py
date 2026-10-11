"""Estimativa e progresso da narração de um PDF inteiro."""

from __future__ import annotations

import bisect
import json
import os
import re
import statistics
from pathlib import Path

from mutagen.mp3 import MP3


TIMING_VERSION = 2


def normalize_speech_text(text: str) -> str:
    """Remove pontilhados de sumário sem alterar o texto visível do PDF."""
    text = re.sub(r"\.{3,}", ",", text)
    text = re.sub(r"\s+,", ",", text)
    return " ".join(text.split())


def is_speakable(text: str) -> bool:
    """As vozes configuradas não geram áudio para blocos sem letras latinas ou números."""
    return bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]", normalize_speech_text(text)))


def estimate_paragraph_ms(text: str) -> int:
    """Estimativa inicial a 1×; a duração real do TTS substitui este valor."""
    if not is_speakable(text):
        return 0
    text = normalize_speech_text(text)
    words = re.findall(r"\b\w+\b", text, flags=re.UNICODE)
    long_words = sum(max(0, len(word) - 7) for word in words)
    pauses = 130 * len(re.findall(r"[,;:]", text))
    pauses += 260 * len(re.findall(r"[.!?]", text))
    return max(650, round(len(words) * 350 + long_words * 20 + pauses))


def write_initial_timing(folder: Path, paragraphs: list) -> None:
    _save(folder / "timing.json", {
        "version": TIMING_VERSION,
        "base_ms": [estimate_paragraph_ms(paragraph.text) for paragraph in paragraphs],
        "voices": {},
    })


def _save(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class DurationTimeline:
    def __init__(self, folder: Path, paragraphs: list, voice: str):
        self.path = folder / "timing.json"
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}
        else:
            self.data = {}
        if (self.data.get("version") != TIMING_VERSION
                or len(self.data.get("base_ms", [])) != len(paragraphs)):
            self.data["version"] = TIMING_VERSION
            self.data["base_ms"] = [estimate_paragraph_ms(item.text) for item in paragraphs]
            self.data.setdefault("voices", {})
            _save(self.path, self.data)
        self.voice = voice
        self.paragraphs = paragraphs
        self.actual = self.data.setdefault("voices", {}).setdefault(voice, {})
        self._recalculate()

    def _recalculate(self) -> None:
        base = self.data["base_ms"]
        known = [(base[int(index)], value) for index, value in self.actual.items()
                 if index.isdigit() and 0 <= int(index) < len(base) and isinstance(value, int)
                 and value > 0 and base[int(index)] >= 5000
                 and not re.search(r"\.{5,}", self.paragraphs[int(index)].text)]
        known_base = sum(item[0] for item in known)
        if known_base:
            observed = min(1.5, max(0.65, statistics.median(real / estimated
                                                            for estimated, real in known)))
            confidence = known_base / (known_base + 120_000)
            factor = 1 + (observed - 1) * confidence
        else:
            factor = 1.0
        self.durations = [0 if value == 0 else self.actual.get(str(index), max(650, round(value * factor)))
                          for index, value in enumerate(base)]
        self.prefix = [0]
        for duration in self.durations:
            self.prefix.append(self.prefix[-1] + duration)

    def record_audio(self, index: int, path: Path) -> bool:
        if not 0 <= index < len(self.durations):
            return False
        try:
            duration = max(1, round(MP3(path).info.length * 1000))
        except Exception:
            return False
        if self.actual.get(str(index)) == duration:
            return False
        self.actual[str(index)] = duration
        _save(self.path, self.data)
        self._recalculate()
        return True

    def duration_ms(self, index: int) -> int:
        return self.durations[index]

    @property
    def is_exact(self) -> bool:
        return len(self.actual) == len(self.durations)

    def total_ms(self, speed: float = 1.0) -> int:
        return round(self.prefix[-1] / speed)

    def position_ms(self, index: int, offset_ms: int = 0, speed: float = 1.0) -> int:
        if index < 0:
            return 0
        index = min(index, len(self.durations) - 1)
        return round((self.prefix[index] + max(0, min(offset_ms, self.durations[index]))) / speed)

    def locate(self, display_ms: int, speed: float = 1.0) -> tuple[int, int]:
        if not self.durations:
            return 0, 0
        media_ms = max(0, min(round(display_ms * speed), self.prefix[-1] - 1))
        index = min(bisect.bisect_right(self.prefix, media_ms) - 1, len(self.durations) - 1)
        return index, media_ms - self.prefix[index]
