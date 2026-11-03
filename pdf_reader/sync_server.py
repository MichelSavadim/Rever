"""Private Rever library API, intended for access through Tailscale Serve."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
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
    def __init__(self, library: Library, token: str, port: int = 8765):
        self.library = library
        self.token = token
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: Thread | None = None

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
                    self.respond(200, {"version": 1, "books": [
                        {"id": book.id, "title": book.title, "page_count": book.page_count,
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
                    if timestamp > state_timestamp(book.reader_state_path):
                        write_json(book.reader_state_path, {
                            "page": page, "paragraph": paragraph, "updated_at": timestamp,
                        })
                    self.respond(200, self.read_state(book.reader_state_path))
                except (ValueError, TypeError, json.JSONDecodeError):
                    self.respond(400, {"error": "Estado inválido"})

            def receive_pdf(self) -> None:
                parts = urlsplit(self.path).path.strip("/").split("/")
                if len(parts) != 3 or not BOOK_ID.fullmatch(parts[1]):
                    self.respond(400, {"error": "Identificador inválido"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 500 * 1024 * 1024:
                        raise ValueError("Tamanho inválido")
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
