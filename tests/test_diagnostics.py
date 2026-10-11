"""Garante que exceções e mensagens Qt gerem um relatório legível."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DiagnosticsTest(unittest.TestCase):
    def test_uncaught_exception_and_qt_warning_are_logged(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env["PDF_READER_LOG_DIR"] = temporary
            exception = subprocess.run(
                [sys.executable, "-c",
                 "from pdf_reader.diagnostics import install; install(); raise RuntimeError('diagnostic sentinel')"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(exception.returncode, 0)
            warning = subprocess.run(
                [sys.executable, "-c",
                 "from pdf_reader.diagnostics import install,install_qt_handler; "
                 "install(); install_qt_handler(); "
                 "from PySide6.QtCore import qWarning; qWarning('qt diagnostic sentinel')"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(warning.returncode, 0, warning.stderr)
            reports = sorted(Path(temporary).glob("crash_*.log"))
            self.assertEqual(len(reports), 2)
            for path in reports:
                self.assertRegex(path.name, r"^crash_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d+)?\.log$")
            contents = [path.read_text(encoding="utf-8") for path in reports]
            self.assertTrue(any("RuntimeError: diagnostic sentinel" in text for text in contents))
            self.assertTrue(any("Qt QtWarningMsg" in text and "qt diagnostic sentinel" in text
                                for text in contents))
            self.assertTrue(all("Python:" in text for text in contents))
            self.assertTrue(any("ENCERRAMENTO após erro" in text for text in contents))


if __name__ == "__main__":
    unittest.main()
