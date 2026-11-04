import json
import tempfile
import unittest
import hashlib
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import quote

import pymupdf

from pdf_reader.core import Library, write_json
from pdf_reader.sync_server import SyncServer


class SyncServerTest(unittest.TestCase):
    def test_authorization_and_newer_state_wins(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            library = Library(Path(directory))
            book_id = "a" * 20
            folder = library.root / book_id
            folder.mkdir()
            (folder / "document.pdf").write_bytes(b"pdf")
            write_json(folder / "book.json", {
                "id": book_id, "title": "Livro", "page_count": 3, "paragraph_count": 2,
            })
            server = SyncServer(library, "secret", 0)
            server.start()
            try:
                base = f"http://127.0.0.1:{server.httpd.server_address[1]}"
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + "/manifest")
                self.assertEqual(error.exception.code, 401)

                def request(path, method="GET", state=None):
                    req = Request(base + path, method=method,
                                  headers={"X-Rever-Token": "secret"},
                                  data=json.dumps(state).encode() if state else None)
                    with urlopen(req) as response:
                        return json.load(response)

                manifest = request("/manifest")
                self.assertEqual(manifest["books"][0]["id"], book_id)
                self.assertEqual(manifest["hidden"], [])
                path = f"/books/{book_id}/state"
                latest = {"page": 2, "paragraph": {"page": 2, "index": 1},
                          "updated_at": 2000}
                self.assertEqual(request(path, "PUT", latest)["page"], 2)
                older = {"page": 1, "paragraph": None, "updated_at": 1000}
                self.assertEqual(request(path, "PUT", older)["page"], 2)
                self.assertEqual(request(path)["updated_at"], 2000)
            finally:
                server.stop()

    def test_upload_and_download_pdf(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            library = Library(Path(directory))
            source = Path(directory) / "livro.pdf"
            pdf = pymupdf.open()
            pdf.new_page().insert_text((72, 72), "Um parágrafo de teste.")
            pdf.save(source)
            pdf.close()
            content = source.read_bytes()
            book_id = hashlib.sha256(content).hexdigest()[:20]
            server = SyncServer(library, "secret", 0)
            server.start()
            try:
                base = f"http://127.0.0.1:{server.httpd.server_address[1]}"
                req = Request(base + f"/books/{book_id}/pdf", data=content, method="PUT",
                              headers={"X-Rever-Token": "secret",
                                       "X-Rever-Title": quote("Livro de teste")})
                with urlopen(req) as response:
                    self.assertEqual(json.load(response)["id"], book_id)
                req = Request(base + f"/books/{book_id}/pdf",
                              headers={"X-Rever-Token": "secret"})
                with urlopen(req) as response:
                    self.assertEqual(response.read(), content)
                self.assertEqual(library.books()[0].title, "Livro de teste")
            finally:
                server.stop()

    def test_playing_pc_has_priority_and_playing_android_can_take_over(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            library = Library(Path(directory))
            book_id = "b" * 20
            folder = library.root / book_id
            folder.mkdir()
            (folder / "document.pdf").write_bytes(b"pdf")
            write_json(folder / "book.json", {
                "id": book_id, "title": "Livro", "page_count": 3, "paragraph_count": 2,
            })
            playing = [book_id]
            server = SyncServer(library, "secret", 0, lambda: playing[0])
            server.start()
            try:
                base = f"http://127.0.0.1:{server.httpd.server_address[1]}"
                path = f"/books/{book_id}/state"

                def put(page, stamp, android_playing=False):
                    body = json.dumps({"page": page, "paragraph": None,
                                       "updated_at": stamp}).encode()
                    req = Request(base + path, data=body, method="PUT", headers={
                        "X-Rever-Token": "secret",
                        "X-Rever-Playing": "1" if android_playing else "0",
                    })
                    with urlopen(req) as response:
                        return json.load(response)

                self.assertEqual(put(2, 2000)["page"], 1)
                self.assertEqual(put(2, 2000, True)["page"], 2)
                playing[0] = None
                taken = put(3, 1000, True)
                self.assertEqual(taken["page"], 3)
                self.assertGreater(taken["updated_at"], 2000)
                req = Request(base + "/manifest", headers={"X-Rever-Token": "secret",
                                                           "X-Rever-Playing-Book": book_id})
                with urlopen(req) as response:
                    json.load(response)
                self.assertTrue(server.android_is_playing(book_id))
                req = Request(base + "/manifest", headers={"X-Rever-Token": "secret"})
                with urlopen(req) as response:
                    json.load(response)
                self.assertFalse(server.android_is_playing(book_id))
            finally:
                server.stop()

    def test_deletion_is_published_and_stale_upload_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            library = Library(Path(directory))
            source = Path(directory) / "livro.pdf"
            pdf = pymupdf.open()
            pdf.new_page().insert_text((72, 72), "Um parágrafo de teste.")
            pdf.save(source)
            pdf.close()
            book = library.import_pdf(source)
            server = SyncServer(library, "secret", 0)
            server.start()
            try:
                base = f"http://127.0.0.1:{server.httpd.server_address[1]}"
                deleted_at = int(time.time() * 1000) + 2000
                body = json.dumps({"updated_at": deleted_at}).encode()
                req = Request(base + f"/books/{book.id}/delete", data=body,
                              method="PUT", headers={"X-Rever-Token": "secret"})
                with urlopen(req) as response:
                    self.assertEqual(json.load(response)["updated_at"], deleted_at)
                self.assertFalse(book.folder.exists())
                req = Request(base + "/manifest", headers={"X-Rever-Token": "secret"})
                with urlopen(req) as response:
                    self.assertEqual(json.load(response)["deleted"][book.id], deleted_at)
                req = Request(base + f"/books/{book.id}/pdf", data=source.read_bytes(),
                              method="PUT", headers={"X-Rever-Token": "secret",
                                                     "X-Rever-Added-At": "1"})
                with self.assertRaises(HTTPError) as error:
                    urlopen(req)
                self.assertEqual(error.exception.code, 409)
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
