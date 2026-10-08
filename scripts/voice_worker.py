"""Chatterbox se ejecuta en un entorno independiente; carga el modelo una sola vez."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import time
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

    profile_path = ROOT / "data/models/cpu-profile.json"
    profile = (
        json.loads(profile_path.read_text(encoding="utf-8"))
        if profile_path.exists()
        else {}
    )
    if request and "cpu_profile" in request:
        profile = request["cpu_profile"]
    waveform_threads = max(
        1, min(int(profile.get("waveform_threads", 6)), os.cpu_count() or 4)
    )
    torch.set_num_threads(waveform_threads)
    # CPU explícita: evita descargar o requerir CUDA en equipos Intel.
    print(
        "Cargando Chatterbox Multilingual (paquete estable 0.1.7, checkpoint V2)…",
        flush=True,
    )
    model = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
    if profile.get("quantized"):
        status(0, "Optimizando el modelo de voz para CPU (INT8)…")
        model.t3.tfmr = torch.ao.quantization.quantize_dynamic(
            model.t3.tfmr, {torch.nn.Linear}, dtype=torch.qint8, inplace=True
        )
    print(
        f"Perfil CPU: {torch.get_num_threads()} hilos · INT8={bool(profile.get('quantized'))}",
        flush=True,
    )
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
    phrase_cache = ROOT / "data" / "cache" / "speech-phrases"
    phrase_cache.mkdir(parents=True, exist_ok=True)
    reference_hash = hashlib.sha256(Path(request["reference"]).read_bytes()).hexdigest()
    started = time.monotonic()
    stage_times = {}
    token_checkpoint = None
    metrics = Path(request["progress"]).with_name("speech-metrics.json")

    def measure_stage(name, function):
        def measured(*args, **kwargs):
            before = time.monotonic()
            try:
                if name == "tokens":
                    torch.set_num_threads(max(1, int(profile.get("threads", 6))))
                    if token_checkpoint and token_checkpoint.exists():
                        checkpoint = torch.load(
                            token_checkpoint, map_location="cpu", weights_only=True
                        )
                        torch.set_rng_state(checkpoint["rng"])
                        return checkpoint["tokens"]
                result = function(*args, **kwargs)
                if name == "tokens" and token_checkpoint:
                    temp = token_checkpoint.with_suffix(".part.pt")
                    torch.save(
                        {"tokens": result.cpu(), "rng": torch.get_rng_state()}, temp
                    )
                    temp.replace(token_checkpoint)
                return result
            finally:
                torch.set_num_threads(waveform_threads)
                elapsed = time.monotonic() - before
                stage_times[name] = stage_times.get(name, 0) + elapsed
                metrics.write_text(
                    json.dumps({"profile": profile, "seconds": stage_times}),
                    encoding="utf-8",
                )
                print(f"Tiempo {name}: {elapsed:.2f} s", flush=True)

        return measured

    model.t3.inference = measure_stage("tokens", model.t3.inference)
    model.s3gen.inference = measure_stage("waveform", model.s3gen.inference)
    for i, item in enumerate(request["items"]):
        pieces = []
        torch.manual_seed(17 + i)
        phrases = list(chunks(item["text"]))
        for phrase_index, text in enumerate(phrases):
            status(
                i,
                f"Voz clonada: escena {i + 1}/{total}, frase {phrase_index + 1}/{len(phrases)}…",
            )
            seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
            signature = json.dumps(
                [
                    reference_hash,
                    request.get("language", "es"),
                    text,
                    seed,
                    0.3,
                    0.5,
                    "v2",
                    bool(profile.get("quantized")),
                    "torch-v2-fp32",
                ],
                ensure_ascii=False,
            )
            cached = phrase_cache / (
                hashlib.sha256(signature.encode()).hexdigest() + ".wav"
            )
            token_signature = json.dumps(
                [
                    reference_hash,
                    request.get("language", "es"),
                    text,
                    seed,
                    bool(profile.get("quantized")),
                    "v2-tokens",
                ]
            )
            token_checkpoint = phrase_cache / (
                hashlib.sha256(token_signature.encode()).hexdigest() + ".tokens.pt"
            )
            if cached.exists():
                waveform, sample_rate = sf.read(cached, dtype="float32")
                if sample_rate != model.sr:
                    raise ValueError("Frecuencia inválida en la caché de frases.")
            else:
                torch.manual_seed(seed)
                audio = model.generate(
                    text,
                    language_id=request.get("language", "es"),
                    cfg_weight=0.3,
                    exaggeration=0.5,
                )
                waveform = audio.squeeze().numpy()
                if not np.isfinite(waveform).all():
                    raise ValueError("El modelo produjo audio inválido.")
                temp = cached.with_suffix(".part.wav")
                sf.write(temp, waveform, model.sr, subtype="PCM_16")
                temp.replace(cached)
            pieces.append(waveform)
            pieces.append(np.zeros(int(model.sr * 0.06), dtype=np.float32))
        if not pieces:
            raise ValueError("La escena no tiene texto.")
        waveform = np.concatenate(pieces[:-1])
        if not np.isfinite(waveform).all():
            raise ValueError(
                "El modelo produjo audio inválido; vuelve a generar esta escena."
            )
        sf.write(item["output"], waveform, model.sr, subtype="PCM_16")
        elapsed = time.monotonic() - started
        remaining = elapsed / (i + 1) * (total - i - 1)
        status(
            i + 1,
            f"Voz clonada: escena {i + 1}/{total} terminada · quedan ~{remaining / 60:.0f} min (estimación)",
        )


if __name__ == "__main__":
    main()
