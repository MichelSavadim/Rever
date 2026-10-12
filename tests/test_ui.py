"""Fluxo do player: geração sob demanda, buffer e limpeza."""

import os
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
import pymupdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QPointF, QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QMessageBox

from pdf_reader.app import MainWindow, clock, generation_error_message
from pdf_reader.core import audio_path
from pdf_reader.viewer import PdfPageView


def pump_until(app, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


class PlayerFlowTest(unittest.TestCase):
    def test_edge_tts_403_has_readable_message(self):
        error = RuntimeError("403, url=wss://speech.platform.bing.com/secret")
        error.status = 403
        message = generation_error_message(error)
        self.assertIn("Edge TTS", message)
        self.assertIn("HTTP 403", message)
        self.assertNotIn("wss://", message)

    def test_theme_viewer_page_and_pdf_search_without_playback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pesquisa.pdf"
            pdf = pymupdf.open()
            pdf.new_page().insert_text((72, 72), "Marcador na primeira página.")
            pdf.new_page().insert_text((72, 72), "Marcador na segunda página.")
            pdf.save(source)
            pdf.close()
            old_library = os.environ.get("PDF_READER_LIBRARY")
            os.environ["PDF_READER_LIBRARY"] = str(root / "library")
            app = QApplication.instance() or QApplication([])
            window = None
            old_theme = None
            try:
                window = MainWindow()
                old_theme = window.theme_action.isChecked()
                book = window.library.import_pdf(source)
                window.load_books(book.id)
                window.show()
                app.processEvents()

                window.theme_action.setChecked(True)
                self.assertTrue(window.viewer._dark_theme)
                self.assertTrue(window.settings.value("dark_theme", False, type=bool))
                self.assertEqual(
                    window.transport_icons["play"].pixmap(22, 22).toImage().pixelColor(10, 11).name(),
                    "#ffffff",
                )
                window.on_playback_state(QMediaPlayer.PlaybackState.PlayingState)
                window.theme_action.setChecked(False)
                self.assertFalse(window.viewer._dark_theme)
                self.assertEqual(
                    window.transport_icons["play"].pixmap(22, 22).toImage().pixelColor(10, 11).name(),
                    "#192334",
                )
                self.assertEqual(window.play_pause_button.icon().cacheKey(),
                                 window.transport_icons["pause"].cacheKey())
                window.on_playback_state(QMediaPlayer.PlaybackState.PausedState)

                window.viewer_page_input.setText("2")
                window.goto_viewer_page()
                self.assertEqual(window.current_page, 2)
                self.assertEqual(window.viewer_page_total.text(), "/ 2")
                self.assertEqual(window.track_index, -1)
                self.assertIsNone(window.pending_play)

                search_action = next(action for action in window.actions()
                                     if action.shortcut().toString() == "Ctrl+F")
                search_action.trigger()
                self.assertTrue(window.search_input.hasFocus())
                window.search_input.setText("Marcador")
                window.search_next()
                self.assertEqual(window.search_count.text(), "1/2")
                self.assertEqual(window.current_page, 1)
                self.assertIsNotNone(window.viewer._search_highlight)
                window.search_next()
                self.assertEqual(window.search_count.text(), "2/2")
                self.assertEqual(window.current_page, 2)
                window.search_previous()
                self.assertEqual(window.search_count.text(), "1/2")
                window.search_input.setText("não existe")
                window.search_next()
                self.assertEqual(window.search_count.text(), "0 resultados")
                self.assertIsNone(window.viewer._search_highlight)
                self.assertEqual(window.track_index, -1)
            finally:
                if window is not None:
                    if old_theme is not None:
                        window.theme_action.setChecked(old_theme)
                    window.close()
                    app.processEvents()
                if old_library is None:
                    os.environ.pop("PDF_READER_LIBRARY", None)
                else:
                    os.environ["PDF_READER_LIBRARY"] = old_library

    def test_reader_position_survives_switch_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = []
            for number, pages in ((1, 3), (2, 2)):
                source = root / f"livro-{number}.pdf"
                pdf = pymupdf.open()
                for page in range(1, pages + 1):
                    pdf.new_page().insert_text((72, 72), f"Livro {number}, página {page}.")
                pdf.save(source)
                pdf.close()
                sources.append(source)

            old_library = os.environ.get("PDF_READER_LIBRARY")
            os.environ["PDF_READER_LIBRARY"] = str(root / "library")
            app = QApplication.instance() or QApplication([])
            window = None
            try:
                window = MainWindow()
                first = window.library.import_pdf(sources[0])
                second = window.library.import_pdf(sources[1])
                window.load_books(first.id)
                window.show()
                app.processEvents()
                window.show_page(2)
                window.select_button.setChecked(True)
                selected = next(i for i, p in enumerate(window.paragraphs) if p.page == 2)
                bbox = window.paragraphs[selected].bbox
                window.on_page_clicked(2, QPointF(bbox[0] + 2, bbox[1] + 2))
                scrollbar = window.viewer.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                self.assertTrue(pump_until(app, lambda: window.current_page == 3))
                window.load_books(second.id)
                saved = json.loads(first.reader_state_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["page"], 3)
                self.assertEqual(saved["paragraph"], {"page": 2, "index": 1})

                window.load_books(first.id)
                self.assertEqual(window.current_page, 3)
                self.assertEqual(window.resume_index, selected)
                self.assertEqual(window.selected_index, selected)
                self.assertIsNotNone(window.viewer._highlight)
                with patch.object(window, "request_play") as request_play:
                    window.toggle_play()
                request_play.assert_called_once_with(selected)
                self.assertEqual(window.current_page, 3)
                window.load_books(second.id)
                window.show_page(2)
                window.close()
                app.processEvents()

                window = MainWindow()
                window.show()
                self.assertTrue(pump_until(app, lambda: window.book is not None
                                           and window.book.id == second.id and window.current_page == 2))
                self.assertEqual(window.library.last_book_id(), second.id)
                self.assertTrue(first.reader_state_path.exists())
                self.assertTrue(window.remove_book_action.isEnabled())
                for label, result in (
                    ("Cancelar", "cancel"),
                    ("Excluir apenas da biblioteca", "keep"),
                    ("Excluir da biblioteca e todos os arquivos", "delete"),
                ):
                    def select_option(expected=label):
                        dialog = app.activeModalWidget()
                        self.assertIsInstance(dialog, QMessageBox)
                        next(button for button in dialog.buttons()
                             if button.text() == expected).click()
                    QTimer.singleShot(0, select_option)
                    self.assertEqual(window.confirm_remove_book(second), result)

                with patch.object(window, "confirm_remove_book", return_value="keep"):
                    window.remove_selected_book()
                self.assertEqual(window.book.id, first.id)
                self.assertTrue(second.reader_state_path.exists())
                self.assertNotIn(second.id, [book.id for book in window.library.books()])

                restored = window.library.import_pdf(sources[1])
                window.load_books(restored.id)
                self.assertEqual(window.current_page, 2)
                with patch.object(window, "confirm_remove_book", return_value="delete"):
                    window.remove_selected_book()
                self.assertFalse(second.folder.exists())
                self.assertEqual(window.book.id, first.id)
            finally:
                if window is not None:
                    window.close()
                    app.processEvents()
                if old_library is None:
                    os.environ.pop("PDF_READER_LIBRARY", None)
                else:
                    os.environ["PDF_READER_LIBRARY"] = old_library

    def test_export_progress_resets_when_switching_books(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = []
            for number in (1, 2):
                source = root / f"livro-{number}.pdf"
                pdf = pymupdf.open()
                pdf.new_page().insert_text((72, 72), f"Texto do livro {number}.")
                pdf.save(source)
                pdf.close()
                sources.append(source)

            old_library = os.environ.get("PDF_READER_LIBRARY")
            os.environ["PDF_READER_LIBRARY"] = str(root / "library")
            app = QApplication.instance() or QApplication([])
            window = None
            try:
                window = MainWindow()
                first = window.library.import_pdf(sources[0])
                second = window.library.import_pdf(sources[1])
                window.load_books(first.id)
                window.progress.setRange(0, 3)
                window.progress.setValue(2)
                window.status_label.setText("Gerando MP3 permanente: 2/3 parágrafos")

                window.load_books(second.id)
                self.assertEqual(window.progress.value(), 0)
                self.assertEqual(window.progress.maximum(), 100)
                self.assertEqual(window.status_label.text(), "Pronto")

                window.progress.setValue(75)
                window.load_books(first.id)
                self.assertEqual(window.progress.value(), 0)
                self.assertEqual(window.status_label.text(), "Pronto")
            finally:
                if window is not None:
                    window.close()
                    app.processEvents()
                if old_library is None:
                    os.environ.pop("PDF_READER_LIBRARY", None)
                else:
                    os.environ["PDF_READER_LIBRARY"] = old_library

    def test_export_can_be_cancelled_with_live_time_estimate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "exportacao.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page()
            for index in range(3):
                page.insert_text((72, 70 + index * 90), f"Parágrafo {index + 1} para exportação.")
            pdf.save(source)
            pdf.close()
            sample_audio = root / "sample.mp3"
            subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", str(sample_audio)],
                check=True,
            )

            block_at = [2]

            def slow_generation(book, paragraphs, voice, progress, *, temporary=False,
                                cancelled=lambda: False):
                if paragraphs[0].index >= block_at[0]:
                    while not cancelled():
                        time.sleep(0.02)
                    return
                target = audio_path(book, voice, paragraphs[0], temporary=temporary)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.is_file():
                    shutil.copy2(sample_audio, target)

            old_library = os.environ.get("PDF_READER_LIBRARY")
            os.environ["PDF_READER_LIBRARY"] = str(root / "library")
            app = QApplication.instance() or QApplication([])
            window = None
            try:
                window = MainWindow()
                book = window.library.import_pdf(source)
                window.load_books(book.id)
                window.show()
                app.processEvents()
                for label, result in (
                    ("Continuar geração", "continue"),
                    ("Cancelar e apagar arquivos gerados", "delete"),
                    ("Cancelar e preservar arquivos gerados", "keep"),
                ):
                    def select_option(expected=label):
                        dialog = app.activeModalWidget()
                        self.assertIsInstance(dialog, QMessageBox)
                        button = next(button for button in dialog.buttons()
                                      if button.text() == expected)
                        button.click()
                    QTimer.singleShot(0, select_option)
                    self.assertEqual(window.confirm_cancel_export(), result)
                self.assertFalse(window.cancel_export_button.isVisible())
                self.assertFalse(window.export_elapsed_label.isVisible())
                self.assertFalse(window.export_remaining_label.isVisible())
                self.assertEqual(window.export_elapsed_label.text(), "Tempo decorrido: 00:00:00")
                self.assertTrue(window.export_remaining_label.text().startswith("Tempo estimado de conclusão:"))
                with patch("pdf_reader.app.run_generation", side_effect=slow_generation):
                    window.start_export("full")
                    self.assertTrue(window.cancel_export_button.isVisible())
                    self.assertTrue(window.export_elapsed_label.isVisible())
                    self.assertTrue(window.export_remaining_label.isVisible())
                    self.assertTrue(window.export_remaining_label.text().startswith("Tempo estimado de conclusão:"))
                    self.assertTrue(window.cancel_export_button.isEnabled())
                    self.assertTrue(pump_until(app, lambda: window.progress.value() == 1))
                    self.assertNotIn("calculando", window.export_remaining_label.text())
                    self.assertTrue(window.export_elapsed_label.text().startswith("Tempo decorrido:"))
                    with patch.object(window, "confirm_cancel_export", return_value="continue"):
                        window.cancel_export_button.click()
                    self.assertFalse(window.worker.cancelled.is_set())
                    with patch.object(window, "confirm_cancel_export", return_value="keep"):
                        window.cancel_export_button.click()
                    self.assertTrue(pump_until(app, lambda: window.thread is None))
                self.assertIn("cancelada", window.status_label.text())
                self.assertFalse(window.cancel_export_button.isVisible())
                self.assertFalse(window.export_elapsed_label.isVisible())
                self.assertFalse(window.export_remaining_label.isVisible())
                self.assertFalse(list((book.folder / "exports").glob("*.mp3")))
                self.assertTrue(audio_path(book, window.current_voice(), window.paragraphs[0]).is_file())

                block_at[0] = 3
                with patch("pdf_reader.app.run_generation", side_effect=slow_generation):
                    window.start_export("full")
                    self.assertTrue(pump_until(app, lambda: window.progress.value() == 2))
                    second = audio_path(book, window.current_voice(), window.paragraphs[1])
                    self.assertTrue(second.is_file())
                    with patch.object(window, "confirm_cancel_export", return_value="delete"):
                        window.cancel_export_button.click()
                    self.assertTrue(pump_until(app, lambda: window.thread is None))
                self.assertFalse(second.exists())
                self.assertTrue(audio_path(book, window.current_voice(), window.paragraphs[0]).is_file())
                self.assertFalse(window.cancel_export_button.isVisible())
                self.assertFalse(window.export_elapsed_label.isVisible())
                self.assertFalse(window.export_remaining_label.isVisible())
            finally:
                if window is not None:
                    window.close()
                    app.processEvents()
                if old_library is None:
                    os.environ.pop("PDF_READER_LIBRARY", None)
                else:
                    os.environ["PDF_READER_LIBRARY"] = old_library

    def test_continuous_pages_and_highlight_layer(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "paginas.pdf"
            pdf = pymupdf.open()
            for number in range(3):
                pdf.new_page().insert_text((72, 72), f"Página {number + 1}")
            pdf.save(source)
            pdf.close()
            app = QApplication.instance() or QApplication([])
            viewer = PdfPageView()
            viewer.resize(700, 600)
            viewer.show()
            viewer.load_pdf(source)
            self.assertTrue(pump_until(app, lambda: len(viewer._rendered) > 0))
            self.assertEqual(len(viewer._page_rects), 3)
            viewer.show_page(2)
            self.assertTrue(pump_until(app, lambda: viewer._current_page == 2))
            viewer.set_highlight(2, (70, 60, 180, 85))
            self.assertLess(viewer._highlight.zValue(), viewer._page_items[2].zValue())
            image = viewer._page_items[2].pixmap().toImage()
            self.assertTrue(image.hasAlphaChannel())
            self.assertEqual(image.pixelColor(20, 20).alpha(), 0)
            viewer.close()

    def test_repeated_buffer_workers_finish_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sequencia.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page()
            for index in range(18):
                page.insert_text((72, 60 + index * 28), f"Parágrafo {index + 1} da sequência.")
            pdf.save(source)
            pdf.close()
            sample_audio = root / "sample.mp3"
            subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(sample_audio)],
                check=True,
            )

            def fake_generation(book, paragraphs, voice, progress, *, temporary=False, cancelled=lambda: False):
                if not cancelled():
                    target = audio_path(book, voice, paragraphs[0], temporary=temporary)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sample_audio, target)
                    progress(1, 1)

            old_library = os.environ.get("PDF_READER_LIBRARY")
            os.environ["PDF_READER_LIBRARY"] = str(root / "library")
            app = QApplication.instance() or QApplication([])
            window = None
            try:
                window = MainWindow()
                book = window.library.import_pdf(source)
                window.load_books(book.id)
                window.show()
                with patch("pdf_reader.app.run_generation", side_effect=fake_generation):
                    for index in range(18):
                        window.request_play(index, autoplay=False)
                        self.assertTrue(
                            pump_until(app, lambda i=index: window.thread is None and window.track_index == i),
                            f"Worker do parágrafo {index + 1} não terminou",
                        )
                window.close()
                self.assertFalse((book.folder / "temp_audio").exists())
            finally:
                if window is not None:
                    window.close()
                    app.processEvents()
                if old_library is None:
                    os.environ.pop("PDF_READER_LIBRARY", None)
                else:
                    os.environ["PDF_READER_LIBRARY"] = old_library

    def test_buffer_highlight_and_temporary_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "amostra.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((72, 72), "Primeiro parágrafo de teste.")
            page.insert_text((72, 150), "Segundo parágrafo de teste.")
            pdf.save(source)
            pdf.close()
            sample_audio = root / "sample.mp3"
            subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=4", str(sample_audio)],
                check=True,
            )

            def fake_generation(book, paragraphs, voice, progress, *, temporary=False, cancelled=lambda: False):
                if cancelled():
                    return
                target = audio_path(book, voice, paragraphs[0], temporary=temporary)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sample_audio, target)
                progress(1, 1)

            old_library = os.environ.get("PDF_READER_LIBRARY")
            os.environ["PDF_READER_LIBRARY"] = str(root / "library")
            app = QApplication.instance() or QApplication([])
            window = None
            try:
                window = MainWindow()
                book = window.library.import_pdf(source)
                window.load_books(book.id)
                window.show()
                app.processEvents()
                for name, icon in window.transport_icons.items():
                    self.assertFalse(icon.isNull(), name)
                    self.assertFalse(icon.pixmap(22, 22).isNull(), name)
                window.on_playback_state(QMediaPlayer.PlaybackState.PlayingState)
                self.assertEqual(window.play_pause_button.toolTip(), "Pausar")
                self.assertEqual(window.play_pause_button.icon().cacheKey(),
                                 window.transport_icons["pause"].cacheKey())
                window.on_playback_state(QMediaPlayer.PlaybackState.PausedState)
                self.assertEqual(window.play_pause_button.toolTip(), "Reproduzir")
                self.assertEqual(window.play_pause_button.icon().cacheKey(),
                                 window.transport_icons["play"].cacheKey())
                header_y = window.collapse_left_button.mapTo(window, QPoint(0, 0)).y()
                open_width = window.horizontal_splitter.sizes()[0]
                window.left_action.setChecked(False)
                halfway_deadline = time.monotonic() + 0.11
                while time.monotonic() < halfway_deadline:
                    app.processEvents()
                    time.sleep(0.01)
                midway_width = window.horizontal_splitter.sizes()[0]
                self.assertLess(midway_width, open_width)
                self.assertGreater(midway_width, 36)
                self.assertTrue(pump_until(app, lambda: not window.left_panel.isVisible()))
                self.assertTrue(window.left_collapsed_holder.isVisible())
                self.assertLessEqual(abs(window.left_collapsed.mapTo(window, QPoint(0, 0)).y() - header_y), 15)
                self.assertLess(window.left_collapsed.mapTo(window, QPoint(0, 0)).x(), 30)
                window.left_action.setChecked(True)
                self.assertTrue(pump_until(app, lambda: "left" not in window.panel_animations))
                self.assertTrue(window.left_panel.isVisible())
                right_header_y = window.collapse_right_button.mapTo(window, QPoint(0, 0)).y()
                window.right_action.setChecked(False)
                self.assertTrue(pump_until(app, lambda: not window.right_panel.isVisible()))
                self.assertTrue(window.right_collapsed_holder.isVisible())
                self.assertLessEqual(abs(window.right_collapsed.mapTo(window, QPoint(0, 0)).y()
                                         - right_header_y), 15)
                self.assertGreater(window.right_collapsed.mapTo(window, QPoint(0, 0)).x(),
                                   window.width() - 50)
                window.right_action.setChecked(True)
                self.assertTrue(pump_until(app, lambda: "right" not in window.panel_animations))
                self.assertTrue(window.right_panel.isVisible())
                window.select_button.setChecked(True)
                bbox = window.paragraphs[0].bbox
                window.on_page_clicked(1, QPointF(bbox[0] + 2, bbox[1] + 2))
                self.assertEqual(window.selected_index, 0)
                self.assertIsNotNone(window.viewer._highlight)
                with patch("pdf_reader.app.run_generation", side_effect=fake_generation):
                    window.toggle_play()
                    self.assertTrue(pump_until(app, lambda: window.thread is None and window.track_index == 0))
                    self.assertGreater(window.seek_slider.maximum(), 0)
                    label = clock(window.timeline.total_ms())
                    if not window.timeline.is_exact:
                        label = f"≈ {label}"
                    self.assertEqual(window.total.text(), label)
                    first_progress = window.seek_slider.value()
                    self.assertTrue(pump_until(app, lambda: window.session_label.text() >= "Ouvido: 00:00:01",
                                               timeout=2))
                    session_before = window.session_label.text()
                    first = audio_path(book, window.current_voice(), window.paragraphs[0], temporary=True)
                    second = audio_path(book, window.current_voice(), window.paragraphs[1], temporary=True)
                    self.assertTrue(first.is_file())
                    self.assertTrue(second.is_file())
                    window.request_play(1)
                    self.assertTrue(pump_until(app, lambda: not first.exists(), timeout=4))
                    self.assertTrue(second.is_file())
                    self.assertGreater(window.seek_slider.value(), first_progress)
                    self.assertGreaterEqual(window.session_label.text(), session_before)
                    original_total = window.seek_slider.maximum()
                    window.speed_combo.setCurrentText("2×")
                    self.assertEqual(window.seek_slider.maximum(), round(original_total / 2))
                window.select_button.setChecked(False)
                self.assertIsNotNone(window.viewer._highlight)
                self.assertEqual(window.track_index, 1)
                another_source = root / "outro.pdf"
                another = pymupdf.open()
                another.new_page().insert_text((72, 72), "Outro documento da biblioteca.")
                another.save(another_source)
                another.close()
                second_book = window.library.import_pdf(another_source)
                window.load_books(second_book.id)
                self.assertEqual(window.book.id, second_book.id)
                self.assertEqual(window.viewer._pdf_path, second_book.pdf_path)
                self.assertIsNone(window.viewer._highlight)
                next_audio = audio_path(second_book, window.current_voice(), window.paragraphs[0], temporary=True)
                next_audio.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sample_audio, next_audio)
                window.toggle_play()
                self.assertFalse(window.select_button.isChecked())
                self.assertEqual(window.track_index, 0)
                self.assertIsNotNone(window.viewer._highlight)
                self.assertFalse((book.folder / "temp_audio").exists())
                window.close()
                self.assertFalse((second_book.folder / "temp_audio").exists())
            finally:
                if window is not None:
                    window.close()
                    app.processEvents()
                if old_library is None:
                    os.environ.pop("PDF_READER_LIBRARY", None)
                else:
                    os.environ["PDF_READER_LIBRARY"] = old_library


if __name__ == "__main__":
    unittest.main()
