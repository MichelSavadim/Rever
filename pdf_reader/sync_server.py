"""Private Rever library API, intended for access through Tailscale Serve."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Callable
from urllib.parse import unquote_plus, urlsplit

from .core import Library, write_json


BOOK_ID = re.compile(r"[0-9a-f]{20}\Z")
MAX_STATE_BYTES = 8192


def state_timestamp(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("updated_at") or path.stat().st_mtime * 1000)
    except (OSError, ValueError, TypeError):
        return 0


class SyncServer:
    def __init__(self, library: Library, token: str, port: int = 8765,
                 playing_book_id: Callable[[], str | None] = lambda: None):
        self.library = library
        self.token = token
        self.port = port
        self.playing_book_id = playing_book_id
        self.android_playing_book_id: str | None = None
        self.android_playing_until = 0.0
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: Thread | None = None

    def report_android_playback(self, book_id: str | None) -> None:
        self.android_playing_book_id = book_id if book_id and BOOK_ID.fullmatch(book_id) else None
        self.android_playing_until = time.monotonic() + 120 if self.android_playing_book_id else 0

    def android_is_playing(self, book_id: str) -> bool:
        return self.android_playing_book_id == book_id and time.monotonic() < self.android_playing_until

    def start(self) -> None:
        if self.httpd:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def respond(self, code: int, data: object) -> None:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorized(self) -> bool:
                if secrets.compare_digest(self.headers.get("X-Rever-Token", ""), owner.token):
                    return True
                self.respond(401, {"error": "Token inválido"})
                return False

            def book(self, path: str):
                parts = path.strip("/").split("/")
                if len(parts) != 3 or parts[0] != "books" or not BOOK_ID.fullmatch(parts[1]):
                    return None, None
                book = next((item for item in owner.library.books() if item.id == parts[1]), None)
                return book, parts[2]

            def do_GET(self) -> None:
                if not self.authorized():
                    return
                path = urlsplit(self.path).path
                if path == "/manifest":
                    owner.report_android_playback(self.headers.get("X-Rever-Playing-Book"))
                    self.respond(200, {"version": 2, "playing_book_id": owner.playing_book_id(),
                        "deleted": owner.library.deleted_books(),
                        "hidden": sorted(owner.library.hidden_books()), "books": [
                        {"id": book.id, "title": book.title, "page_count": book.page_count,
                         "added_at": int((book.folder / "book.json").stat().st_mtime * 1000),
                         "pdf_size": book.pdf_path.stat().st_size,
                         "state": self.read_state(book.reader_state_path)}
                        for book in owner.library.books()
                    ]})
                    return
                book, resource = self.book(path)
                if book is None:
                    self.respond(404, {"error": "PDF não encontrado"})
                elif resource == "pdf":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Length", str(book.pdf_path.stat().st_size))
                    self.end_headers()
                    with book.pdf_path.open("rb") as source:
                        while block := source.read(1024 * 1024):
                            self.wfile.write(block)
                elif resource == "state":
                    self.respond(200, self.read_state(book.reader_state_path))
                else:
                    self.respond(404, {"error": "Recurso não encontrado"})

            def read_state(self, path: Path) -> dict:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        data["updated_at"] = state_timestamp(path)
                        return data
                except (OSError, ValueError):
                    pass
                return {"page": 1, "paragraph": None, "updated_at": 0}

            def do_PUT(self) -> None:
                if not self.authorized():
                    return
                if urlsplit(self.path).path.startswith("/books/") and urlsplit(self.path).path.endswith("/delete"):
                    self.receive_delete()
                    return
                if urlsplit(self.path).path.startswith("/books/") and urlsplit(self.path).path.endswith("/pdf"):
                    self.receive_pdf()
                    return
                book, resource = self.book(urlsplit(self.path).path)
                if book is None or resource != "state":
                    self.respond(404, {"error": "PDF não encontrado"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_STATE_BYTES:
                        raise ValueError("Tamanho inválido")
                    state = json.loads(self.rfile.read(length))
                    page = state.get("page")
                    timestamp = state.get("updated_at")
                    paragraph = state.get("paragraph")
                    if (not isinstance(page, int) or not 1 <= page <= book.page_count or
                            not isinstance(timestamp, int) or timestamp <= 0 or
                            paragraph is not None and (not isinstance(paragraph, dict) or
                            not isinstance(paragraph.get("page"), int) or
                            not isinstance(paragraph.get("index"), int))):
                        raise ValueError("Estado inválido")
                    pc_playing = owner.playing_book_id() == book.id
                    android_playing = self.headers.get("X-Rever-Playing") == "1"
                    owner.report_android_playback(book.id if android_playing else None)
                    if pc_playing and not android_playing:
                        self.respond(200, self.read_state(book.reader_state_path))
                        return
                    current_stamp = state_timestamp(book.reader_state_path)
                    if android_playing and not pc_playing:
                        timestamp = max(timestamp, current_stamp + 1)
                    if timestamp > current_stamp:
                        write_json(book.reader_state_path, {
                            "page": page, "paragraph": paragraph, "updated_at": timestamp,
                        })
                    self.respond(200, self.read_state(book.reader_state_path))
                except (ValueError, TypeError, json.JSONDecodeError):
                    self.respond(400, {"error": "Estado inválido"})

            def receive_delete(self) -> None:
                parts = urlsplit(self.path).path.strip("/").split("/")
                if len(parts) != 3 or parts[0] != "books" or not BOOK_ID.fullmatch(parts[1]):
                    self.respond(400, {"error": "Identificador inválido"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 128:
                        raise ValueError("Tamanho inválido")
                    timestamp = json.loads(self.rfile.read(length)).get("updated_at")
                    if not isinstance(timestamp, int) or timestamp <= 0:
                        raise ValueError("Horário inválido")
                    if owner.playing_book_id() == parts[1]:
                        self.respond(409, {"error": "PDF em reprodução no PC"})
                        return
                    folder = owner.library.root / parts[1]
                    if (folder / "book.json").is_file() and int(
                            (folder / "book.json").stat().st_mtime * 1000) > timestamp:
                        self.respond(409, {"error": "PDF adicionado depois da exclusão"})
                        return
                    if timestamp > owner.library.deleted_books().get(parts[1], 0):
                        owner.library.delete_synced(parts[1], timestamp)
                    self.respond(200, {"updated_at": owner.library.deleted_books().get(parts[1], 0)})
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    self.respond(400, {"error": str(exc)})

            def receive_pdf(self) -> None:
                parts = urlsplit(self.path).path.strip("/").split("/")
                if len(parts) != 3 or not BOOK_ID.fullmatch(parts[1]):
                    self.respond(400, {"error": "Identificador inválido"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 500 * 1024 * 1024:
                        raise ValueError("Tamanho inválido")
                    if parts[1] in owner.library.hidden_books():
                        self.respond(409, {"error": "PDF oculto no PC"})
                        return
                    added_at = int(self.headers.get("X-Rever-Added-At", "0"))
                    deleted_at = owner.library.deleted_books().get(parts[1], 0)
                    if deleted_at and added_at <= deleted_at:
                        self.respond(409, {"error": "PDF removido após esta cópia"})
                        return
                    title = unquote_plus(self.headers.get("X-Rever-Title", "PDF"))[:180].strip() or "PDF"
                    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).rstrip(". ") or "PDF"
                    with tempfile.TemporaryDirectory() as directory:
                        source = Path(directory) / f"{title}.pdf"
                        digest = hashlib.sha256()
                        with source.open("wb") as target:
                            remaining = length
                            while remaining:
                                block = self.rfile.read(min(1024 * 1024, remaining))
                                if not block:
                                    raise ValueError("Transferência interrompida")
                                target.write(block)
                                digest.update(block)
                                remaining -= len(block)
                        if digest.hexdigest()[:20] != parts[1]:
                            raise ValueError("SHA-256 não confere")
                        book = owner.library.import_pdf(source)
                    self.respond(200, {"id": book.id})
                except (ValueError, OSError) as exc:
                    self.respond(400, {"error": str(exc)})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        self.thread = Thread(target=self.httpd.serve_forever, name="ReverSync", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.thread = None


def new_token() -> str:
    return secrets.token_urlsafe(32)
