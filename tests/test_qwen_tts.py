"""Roteamento do Qwen e divisão de textos longos sem iniciar o modelo."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdf_reader.core import Book, Paragraph, audio_path, generate_audio
from pdf_reader.qwen_backend import VOICES
from pdf_reader.qwen_worker import batches, chunks


class QwenFlowTest(unittest.TestCase):
    def test_invalid_unicode_from_pdf_is_replaced_before_tokenization(self):
        self.assertEqual(chunks("Texto \ud800 para narrar."), ["Texto ? para narrar."])

    def test_long_paragraph_is_split_without_losing_words(self):
        text = " ".join(["A leitura em português deve ser clara."] * 35)
        pieces = chunks(text, limit=140)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(piece) <= 140 for piece in pieces))
        self.assertEqual(" ".join(pieces), text)

    def test_sentences_are_batched_in_order(self):
        text = (
            "Esta é uma frase inicial completa. Outra frase com palavras suficientes! "
            "Mais uma frase para o teste. E também esta frase um pouco maior? "
            "Última frase, igualmente completa."
        )
        pieces = chunks(text)
        self.assertEqual(len(pieces), 5)
        self.assertEqual(" ".join(pieces), text)
        self.assertEqual(batches(pieces, size=3), [pieces[:3], pieces[3:]])

    def test_qwen_voice_generates_its_own_audio_path(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Book("book", "Livro", 1, 1, Path(directory))
            paragraph = Paragraph(1, 1, "Olá, mundo.")
            voice = VOICES["Ryan (Qwen local)"]

            def fake_qwen(_text, _voice, target, _cancelled):
                target.write_bytes(b"mp3-test")

            with (patch("pdf_reader.core.synthesize_qwen_mp3", side_effect=fake_qwen) as qwen,
                  patch("pdf_reader.core.synthesize_mp3") as piper):
                generate_audio(book, [paragraph], voice, lambda _done, _total: None)
            qwen.assert_called_once()
            piper.assert_not_called()
            self.assertEqual(audio_path(book, voice, paragraph).read_bytes(), b"mp3-test")


if __name__ == "__main__":
    unittest.main()
