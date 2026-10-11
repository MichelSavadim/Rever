import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from pdf_reader.paths import data_root, resource_root


class PathsTests(unittest.TestCase):
    def test_source_uses_project_folder(self):
        self.assertEqual(data_root(), resource_root())

    def test_bundled_app_writes_next_to_executable(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", r"C:\Rever\Rever.exe"
        ):
            self.assertEqual(data_root(), Path(r"C:\Rever"))
