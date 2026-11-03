"""Processo isolado do Qwen3-TTS; troca mensagens JSON por stdin/stdout."""

from __future__ import annotations

import contextlib
import json
import re
import sys
import traceback
from pathlib import Path


protocol = sys.stdout


def send(message: dict) -> None:
    protocol.write(json.dumps(message, ensure_ascii=False) + "\n")
    protocol.flush()


def chunks(text: str, limit: int = 140) -> list[str]:
    # Alguns PDFs contêm substitutos Unicode isolados, rejeitados pelo tokenizador.
    text = text.encode("utf-8", errors="replace").decode("utf-8")
    sentences = re.split(r"(?<=[.!?;:])\s+", " ".join(text.split()))
    result: list[str] = []
    for sentence in sentences:
        if not sentence:
            continue
        if result and len(result[-1]) < 25 and len(result[-1]) + len(sentence) + 1 <= limit:
            result[-1] += " " + sentence
            continue
        while len(sentence) > limit:
            split = sentence.rfind(" ", 0, limit + 1)
            split = split if split > limit // 2 else limit
            result.append(sentence[:split].strip())
            sentence = sentence[split:].strip()
        if sentence:
            result.append(sentence)
    return result


def batches(items: list[str], size: int = 5) -> list[list[str]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def main() -> None:
    import numpy as np
    import soundfile as sf
    import torch
    from transformers import AutoTokenizer

    with contextlib.redirect_stdout(sys.stderr):
        from qwen_tts import Qwen3TTSModel

        use_cuda = torch.cuda.is_available()
        model = Qwen3TTSModel.from_pretrained(
            sys.argv[1], device_map="cuda:0" if use_cuda else "cpu",
            dtype=torch.bfloat16 if use_cuda else torch.float32,
            attn_implementation="sdpa",
            low_cpu_mem_usage=True,
        )
        # A variante rápida (Rust) pode rejeitar texto extraído de certos PDFs.
        # A variante Python usa o mesmo vocabulário e foi testada na geração.
        model.processor.tokenizer = AutoTokenizer.from_pretrained(
            sys.argv[1], use_fast=False,
        )
    send({"status": "ready"})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            pieces = []
            sample_rate = 0
            for batch in batches(chunks(request["text"])):
                with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
                    waves, sample_rate = model.generate_custom_voice(
                        text=batch, language=["Portuguese"] * len(batch),
                        speaker=[request["speaker"]] * len(batch),
                    )
                for wave in waves:
                    pieces.append(np.asarray(wave, dtype=np.float32))
                    pieces.append(np.zeros(round(sample_rate * 0.12), dtype=np.float32))
            if not pieces:
                raise ValueError("O parágrafo não contém texto narrável.")
            output = Path(request["output"])
            sf.write(str(output), np.concatenate(pieces[:-1]), sample_rate)
            send({"status": "ok"})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send({"status": "error", "error": f"Qwen: {exc}"})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        send({"status": "error", "error": f"Não foi possível iniciar o Qwen: {exc}"})
