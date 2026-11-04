"""O motor online usa vozes próprias e o fluxo normal de MP3s."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdf_reader.core import Book, Paragraph, audio_path, generate_audio
from pdf_reader.edge_backend import VOICES, is_edge_voice


class EdgeTtsTest(unittest.TestCase):
    def test_edge_voice_generates_audio_in_its_own_directory(self):
        voice = VOICES["Português (Brasil)"]["Francisca (Edge TTS · online)"]
        self.assertTrue(is_edge_voice(voice))
        with tempfile.TemporaryDirectory() as directory:
            book = Book("book", "Livro", 1, 1, Path(directory))
            paragraph = Paragraph(1, 1, "Olá, mundo.")

            async def save_mp3(destination):
                Path(destination).write_bytes(b"mp3-test")

            with patch("pdf_reader.edge_backend.edge_tts.Communicate") as communicate:
                communicate.return_value.save.side_effect = save_mp3
                generate_audio(book, [paragraph], voice, lambda _done, _total: None)

            self.assertEqual(audio_path(book, voice, paragraph).read_bytes(), b"mp3-test")
            communicate.assert_called_once_with("Olá, mundo.", voice="pt-BR-FranciscaNeural")


if __name__ == "__main__":
    unittest.main()
