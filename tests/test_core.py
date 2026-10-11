"""Verificações de importação e exportação com um PDF real pequeno."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import imageio_ffmpeg
import pymupdf

from pdf_reader.core import (
    GenerationCancelled, Library, Paragraph, audio_path, export_audio, load_paragraphs,
)
from pdf_reader.timing import (
    DurationTimeline, estimate_paragraph_ms, is_speakable, normalize_speech_text,
    write_initial_timing,
)


class LibraryFlowTest(unittest.TestCase):
    def test_sumario_dots_and_unsupported_script_do_not_inflate_audio(self):
        dotted = "Parte 1 ................................................................ 12"
        self.assertEqual(normalize_speech_text(dotted), "Parte 1, 12")
        self.assertLess(estimate_paragraph_ms(dotted), 4000)
        self.assertFalse(is_speakable("オーバーロード"))
        self.assertEqual(estimate_paragraph_ms("オーバーロード"), 0)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "timing.json").write_text(
                '{"base_ms":[1400000,650],"voices":{}}', encoding="utf-8"
            )
            timeline = DurationTimeline(folder, [Paragraph(1, 1, dotted),
                                                 Paragraph(2, 1, "オーバーロード")],
                                        "pt-BR-AntonioNeural")
            self.assertLess(timeline.total_ms(), 4000)
            self.assertEqual(timeline.duration_ms(1), 0)
            self.assertEqual(timeline.locate(timeline.total_ms()), (0, timeline.duration_ms(0) - 1))

    def test_sumario_audio_does_not_calibrate_entire_book(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            paragraphs = [
                Paragraph(1, 1, "Um parágrafo de prosa com palavras suficientes para servir de amostra de leitura."),
                Paragraph(2, 1, "Parte 1 " + "." * 140 + " 12"),
                Paragraph(3, 1, "Outro parágrafo de prosa com palavras suficientes para testar a previsão do livro."),
            ]
            write_initial_timing(folder, paragraphs)
            path = folder / "timing.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            base = data["base_ms"]
            data["voices"] = {"pt-BR-AntonioNeural": {"0": round(base[0] * 1.1),
                                                        "1": round(base[1] * 5)}}
            path.write_text(json.dumps(data), encoding="utf-8")
            timeline = DurationTimeline(folder, paragraphs, "pt-BR-AntonioNeural")
            self.assertLess(timeline.duration_ms(2), base[2] * 1.2)

    def test_import_deduplicates_and_exports_page_range(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "O'Connor"
            root.mkdir()
            source = root / "exemplo.pdf"
            pdf = pymupdf.open()
            for number in (1, 2):
                page = pdf.new_page()
                page.insert_text((72, 72), f"Página {number}: primeiro parágrafo.")
                page.insert_text((72, 150), f"Página {number}: segundo parágrafo.")
            pdf.save(source)
            pdf.close()

            library = Library(root / "library")
            book = library.import_pdf(source)
            self.assertEqual(library.import_pdf(source).id, book.id)
            self.assertEqual(book.page_count, 2)
            self.assertEqual(book.paragraph_count, 4)
            self.assertTrue(book.pdf_path.is_file())
            self.assertTrue(book.thumbnail_path.is_file())

            paragraphs = load_paragraphs(book)
            self.assertTrue((book.folder / "timing.json").is_file())
            timeline = DurationTimeline(book.folder, paragraphs, "pt-BR-FranciscaNeural")
            self.assertGreater(timeline.total_ms(), 0)
            self.assertEqual(timeline.total_ms(2), round(timeline.total_ms() / 2))
            index, offset = timeline.locate(timeline.position_ms(2, 200))
            self.assertEqual((index, offset), (2, 200))
            self.assertEqual([item.page for item in paragraphs], [1, 1, 2, 2])
            selected = [item for item in paragraphs if item.page == 2]
            voice = "pt-BR-FranciscaNeural"
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            for paragraph in selected:
                target = audio_path(book, voice, paragraph)
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                     "sine=frequency=440:duration=0.25", "-b:a", "48k", str(target)],
                    check=True,
                )
            self.assertTrue(timeline.record_audio(2, audio_path(book, voice, selected[0])))
            self.assertEqual(DurationTimeline(book.folder, paragraphs, voice).duration_ms(2),
                             timeline.duration_ms(2))
            with self.assertRaises(GenerationCancelled):
                export_audio(book, selected, voice, "paginas-0002-0002", cancelled=lambda: True)
            self.assertFalse((book.folder / "exports" / f"paginas-0002-0002-{voice}.mp3").exists())
            result = export_audio(book, selected, voice, "paginas-0002-0002")
            self.assertTrue(result.is_file())
            self.assertGreater(result.stat().st_size, 0)

    def test_rejects_pdf_without_extractable_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sem-texto.pdf"
            pdf = pymupdf.open()
            pdf.new_page()
            pdf.save(source)
            pdf.close()
            with self.assertRaisesRegex(ValueError, "não contém texto extraível"):
                Library(root / "library").import_pdf(source)

    def test_remove_from_library_preserves_files_until_full_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "livro.pdf"
            pdf = pymupdf.open()
            pdf.new_page().insert_text((72, 72), "Um texto para retomar mais tarde.")
            pdf.save(source)
            pdf.close()
            library = Library(root / "library")
            book = library.import_pdf(source)
            book.reader_state_path.write_text('{"page": 1}', encoding="utf-8")
            audio = book.folder / "audio" / "exemplo.mp3"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"audio existente")
            library.set_last_book(book.id)

            library.remove(book, delete_files=False)
            self.assertEqual(library.books(), [])
            self.assertIsNone(library.last_book_id())
            self.assertTrue(book.pdf_path.exists())
            self.assertTrue(book.reader_state_path.exists())
            self.assertTrue(audio.exists())

            restored = library.import_pdf(source)
            self.assertEqual(restored.id, book.id)
            self.assertEqual(len(library.books()), 1)
            self.assertTrue(audio.exists())
            self.assertTrue(book.reader_state_path.exists())

            library.remove(restored, delete_files=True)
            self.assertFalse(book.folder.exists())
            self.assertEqual(library.books(), [])


if __name__ == "__main__":
    unittest.main()
