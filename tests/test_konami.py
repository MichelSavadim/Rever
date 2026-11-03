"""Verifica o desbloqueio oculto pelas teclas físicas do teclado."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdf_reader.app import MainWindow


class KonamiTest(unittest.TestCase):
    def test_sequence_unlocks_developer_options(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            with patch.dict(os.environ, {"PDF_READER_LIBRARY": directory}), \
                 patch("pdf_reader.app.QSettings", return_value=settings):
                window = MainWindow()
                window.show()
                app.processEvents()
                try:
                    self.assertFalse(window.developer_action.isVisible())
                    window.goto_input.setFocus()
                    app.processEvents()
                    for key in window._konami:
                        QTest.keyClick(window.goto_input, key)
                    app.processEvents()
                    self.assertTrue(window.developer_action.isVisible())
                    self.assertTrue(settings.value("developer_options", False, type=bool))
                finally:
                    window.close()


if __name__ == "__main__":
    unittest.main()
