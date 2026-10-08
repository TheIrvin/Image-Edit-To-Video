"""Chatterbox se ejecuta en un entorno independiente; carga el modelo una sola vez."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "data" / "models"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def chunks(text: str, limit: int = 220):
    # Segmentación, sin resumir, traducir ni quitar palabras de la narración.
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text.strip())
    buffer = ""
    for part in parts:
        words = part.split()
        for word in words:
            if buffer and len(buffer) + len(word) + 1 > limit:
                yield buffer
                buffer = ""
            buffer = (buffer + " " + word).strip()
        if buffer and re.search(r"[.!?;]$", buffer):
            yield buffer
            buffer = ""
    if buffer:
        yield buffer


def main():
    preparing = sys.argv[1] == "--prepare"
    request = (
        None if preparing else json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    )

    def status(done, message):
        if request is None:
            return
        progress = Path(request["progress"])
        temp = progress.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                {"done": done, "total": len(request["items"]), "message": message}
            ),
            encoding="utf-8",
        )
        temp.replace(progress)

    status(0, "Cargando el modelo de voz en CPU…")
    import numpy as np
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    torch.set_num_threads(max(1, min(6, (os.cpu_count() or 4) // 2)))
    # CPU explícita: evita descargar o requerir CUDA en equipos Intel.
    print(
        "Cargando Chatterbox Multilingual (paquete estable 0.1.7, checkpoint V2)…",
        flush=True,
    )
    model = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
    if preparing:
        marker = ROOT / "data" / "models" / "chatterbox-ready.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "model": "Chatterbox Multilingual V2",
                    "device": "cpu",
                    "package": "0.1.7",
                }
            ),
            encoding="utf-8",
        )
        return
    status(0, "Analizando la muestra de voz…")
    model.prepare_conditionals(request["reference"], exaggeration=0.5)
    total = len(request["items"])
    for i, item in enumerate(request["items"]):
        pieces = []
        torch.manual_seed(17 + i)
        phrases = list(chunks(item["text"]))
        for phrase_index, text in enumerate(phrases):
            status(
                i,
                f"Voz clonada: escena {i + 1}/{total}, frase {phrase_index + 1}/{len(phrases)}…",
            )
            audio = model.generate(
                text,
                language_id=request.get("language", "es"),
                cfg_weight=0.3,
                exaggeration=0.5,
            )
            pieces.append(audio.squeeze().numpy())
            pieces.append(np.zeros(int(model.sr * 0.06), dtype=np.float32))
        if not pieces:
            raise ValueError("La escena no tiene texto.")
        waveform = np.concatenate(pieces[:-1])
        if not np.isfinite(waveform).all():
            raise ValueError(
                "El modelo produjo audio inválido; vuelve a generar esta escena."
            )
        sf.write(item["output"], waveform, model.sr, subtype="PCM_16")
        status(i + 1, f"Voz clonada: escena {i + 1}/{total} terminada")


if __name__ == "__main__":
    main()
