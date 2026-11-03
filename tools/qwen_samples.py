"""Gera duas amostras curtas para comparar as vozes locais do Qwen."""

from pathlib import Path

from pdf_reader.qwen_backend import VOICES, shutdown, synthesize_mp3


OUTPUT = Path(__file__).resolve().parent.parent / "output" / "qwen_samples"
TEXT = (
    "A leitura começa em uma manhã tranquila. As palavras seguem com clareza, "
    "enquanto a história ganha ritmo e convida você a continuar."
)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        for name, voice in VOICES.items():
            path = OUTPUT / f"{voice}.mp3"
            print(f"Gerando {name}…", flush=True)
            synthesize_mp3(TEXT, voice, path)
            print(path, flush=True)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
