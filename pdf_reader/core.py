"""Biblioteca local, extração de texto e geração de áudio."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import edge_tts
import imageio_ffmpeg
import pymupdf

from .timing import is_speakable, normalize_speech_text, write_initial_timing


VOICES = {
    "Português (Brasil)": {
        "Francisca": "pt-BR-FranciscaNeural",
        "Antônio": "pt-BR-AntonioNeural",
    },
    "English (US)": {
        "Aria": "en-US-AriaNeural",
        "Guy": "en-US-GuyNeural",
    },
    "Español (España)": {
        "Elvira": "es-ES-ElviraNeural",
        "Álvaro": "es-ES-AlvaroNeural",
    },
}


class GenerationCancelled(Exception):
    """Interrompe a montagem de uma exportação sem publicar um MP3 parcial."""


@dataclass(frozen=True)
class Paragraph:
    page: int  # 1-based
    index: int  # 1-based within page
    text: str
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class Book:
    id: str
    title: str
    page_count: int
    paragraph_count: int
    folder: Path

    @property
    def pdf_path(self) -> Path:
        return self.folder / "document.pdf"

    @property
    def thumbnail_path(self) -> Path:
        return self.folder / "thumbnail.png"

    @property
    def reader_state_path(self) -> Path:
        return self.folder / "reader_state.json"


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _catalog(self) -> dict:
        path = self.root / "library.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def last_book_id(self) -> str | None:
        value = self._catalog().get("last_book_id")
        return value if isinstance(value, str) else None

    def set_last_book(self, book_id: str | None) -> None:
        catalog = self._catalog()
        catalog["last_book_id"] = book_id
        write_json(self.root / "library.json", catalog)

    def books(self) -> list[Book]:
        result = []
        hidden = set(self._catalog().get("hidden_ids", []))
        for metadata_path in self.root.glob("*/book.json"):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                folder = metadata_path.parent
                if data.get("id") not in hidden and (folder / "document.pdf").is_file():
                    result.append(Book(folder=folder, **data))
            except (OSError, ValueError, TypeError):
                continue
        return sorted(result, key=lambda book: book.title.casefold())

    def migrate_from(self, old_root: Path) -> None:
        """Copia a biblioteca da primeira versão sem remover os dados antigos."""
        old_root = Path(old_root)
        if old_root.resolve() == self.root.resolve() or not old_root.is_dir():
            return
        for metadata in old_root.glob("*/book.json"):
            target = self.root / metadata.parent.name
            if not target.exists():
                shutil.copytree(metadata.parent, target)

    def import_pdf(self, source: Path) -> Book:
        source = Path(source)
        if not source.is_file() or source.suffix.lower() != ".pdf":
            raise ValueError("Selecione um arquivo PDF válido.")

        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        book_id = digest.hexdigest()[:20]
        folder = self.root / book_id
        existing = folder / "book.json"
        if existing.exists():
            data = json.loads(existing.read_text(encoding="utf-8"))
            catalog = self._catalog()
            hidden = set(catalog.get("hidden_ids", []))
            if book_id in hidden:
                hidden.remove(book_id)
                catalog["hidden_ids"] = sorted(hidden)
                write_json(self.root / "library.json", catalog)
            return Book(folder=folder, **data)

        with pymupdf.open(source) as pdf:
            if pdf.is_encrypted:
                raise ValueError("PDFs protegidos por senha ainda não são compatíveis.")
            paragraphs = extract_paragraphs(pdf)
            if not paragraphs:
                raise ValueError("O PDF não contém texto extraível. PDFs digitalizados precisarão de OCR.")
            page_count = len(pdf)
            cover = pdf[0].get_pixmap(matrix=pymupdf.Matrix(0.35, 0.35), alpha=False).tobytes("png")

        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, folder / "document.pdf")
        (folder / "thumbnail.png").write_bytes(cover)
        data = {
            "id": book_id,
            "title": source.stem,
            "page_count": page_count,
            "paragraph_count": len(paragraphs),
        }
        write_json(folder / "paragraphs.json", [asdict(p) for p in paragraphs])
        write_initial_timing(folder, paragraphs)
        write_json(existing, data)
        return Book(folder=folder, **data)

    def remove(self, book: Book, *, delete_files: bool) -> None:
        root = self.root.resolve()
        folder = book.folder.resolve()
        if folder.parent != root or folder.name != book.id:
            raise ValueError("O PDF selecionado não pertence a esta biblioteca.")
        catalog = self._catalog()
        hidden = set(catalog.get("hidden_ids", []))
        if delete_files:
            shutil.rmtree(folder)
            hidden.discard(book.id)
        else:
            clear_temporary(book)
            hidden.add(book.id)
        catalog["hidden_ids"] = sorted(hidden)
        if catalog.get("last_book_id") == book.id:
            catalog["last_book_id"] = None
        write_json(self.root / "library.json", catalog)


def extract_paragraphs(pdf: pymupdf.Document) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    for page_number, page in enumerate(pdf, start=1):
        index = 0
        for block in page.get_text("blocks", sort=True):
            if block[6] != 0:
                continue
            text = " ".join(block[4].split())
            if text:
                index += 1
                paragraphs.append(Paragraph(page_number, index, text, tuple(block[:4])))
    return paragraphs


def load_paragraphs(book: Book) -> list[Paragraph]:
    data = json.loads((book.folder / "paragraphs.json").read_text(encoding="utf-8"))
    if data and "bbox" not in data[0]:
        with pymupdf.open(book.pdf_path) as pdf:
            paragraphs = extract_paragraphs(pdf)
        write_json(book.folder / "paragraphs.json", [asdict(p) for p in paragraphs])
        return paragraphs
    return [Paragraph(**item) for item in data]


def audio_path(book: Book, voice: str, paragraph: Paragraph, *, temporary: bool = False) -> Path:
    directory = "temp_audio" if temporary else "audio"
    return book.folder / directory / voice / f"p{paragraph.page:04d}-{paragraph.index:04d}.mp3"


def playable_audio_path(book: Book, voice: str, paragraph: Paragraph) -> Path | None:
    for temporary in (False, True):
        path = audio_path(book, voice, paragraph, temporary=temporary)
        if path.is_file() and path.stat().st_size:
            return path
    return None


def clear_temporary(book: Book) -> bool:
    directory = book.folder / "temp_audio"
    if not directory.exists():
        return True
    try:
        shutil.rmtree(directory)
    except OSError:
        return False
    return True


def clear_all_temporary(library: Library) -> bool:
    cleared = True
    for book in library.books():
        cleared = clear_temporary(book) and cleared
    return cleared


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


async def generate_audio(
    book: Book,
    paragraphs: list[Paragraph],
    voice: str,
    progress: Callable[[int, int], None],
    *,
    temporary: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
) -> None:
    total = len(paragraphs)
    for position, paragraph in enumerate(paragraphs, start=1):
        if cancelled():
            return
        if not is_speakable(paragraph.text):
            progress(position, total)
            continue
        target = audio_path(book, voice, paragraph, temporary=temporary)
        if not target.is_file() or target.stat().st_size == 0:
            target.parent.mkdir(parents=True, exist_ok=True)
            part_file = target.with_suffix(".part.mp3")
            try:
                reusable = audio_path(book, voice, paragraph, temporary=True) if not temporary else None
                if reusable and reusable.is_file():
                    shutil.copy2(reusable, part_file)
                else:
                    await edge_tts.Communicate(normalize_speech_text(paragraph.text), voice).save(str(part_file))
                if cancelled():
                    return
                if part_file.stat().st_size == 0:
                    raise RuntimeError("O serviço TTS retornou um arquivo vazio.")
                os.replace(part_file, target)
            finally:
                part_file.unlink(missing_ok=True)
        progress(position, total)


def export_audio(
    book: Book, paragraphs: list[Paragraph], voice: str, label: str,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Junta MP3s já gerados sem recodificar o áudio."""
    paragraphs = [paragraph for paragraph in paragraphs if is_speakable(paragraph.text)]
    if not paragraphs:
        raise ValueError("O intervalo escolhido não contém texto.")
    files = [audio_path(book, voice, paragraph) for paragraph in paragraphs]
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError("Há parágrafos sem áudio gerado.")
    if cancelled():
        raise GenerationCancelled()
    output_dir = book.folder / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{label}-{voice}.mp3"
    with tempfile.TemporaryDirectory(dir=output_dir) as temp_dir:
        temp = Path(temp_dir)
        list_file = temp / "files.txt"
        # O demuxer concat aceita caminhos absolutos com barras normais no Windows.
        # Aspas simples internas seguem o escape documentado do formato concat.
        entries = []
        for path in files:
            escaped = path.resolve().as_posix().replace("'", "'\\''")
            entries.append(f"file '{escaped}'\n")
        list_file.write_text("".join(entries), encoding="utf-8")
        output = temp / "joined.mp3"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output),
        ]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) as process:
            while True:
                try:
                    _, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if cancelled():
                        process.kill()
                        process.communicate()
                        raise GenerationCancelled()
        if cancelled():
            raise GenerationCancelled()
        if process.returncode != 0:
            raise RuntimeError(f"Não foi possível juntar os MP3s: {stderr.strip()}")
        os.replace(output, target)
    return target


def run_generation(
    book: Book, paragraphs: list[Paragraph], voice: str,
    progress: Callable[[int, int], None], *, temporary: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
) -> None:
    asyncio.run(generate_audio(book, paragraphs, voice, progress, temporary=temporary, cancelled=cancelled))
