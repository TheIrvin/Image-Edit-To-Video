"""Spanish MeloTTS + OpenVoice V2, with no ASR or unrelated language models."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "data/models"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def prepare_files():
    from huggingface_hub import hf_hub_download

    folder = ROOT / "data/models/openvoice-v2"
    for name in (
        "converter/config.json",
        "converter/checkpoint.pth",
        "base_speakers/ses/es.pth",
    ):
        if not (folder / name).is_file():
            hf_hub_download("myshell-ai/OpenVoiceV2", name, local_dir=folder)
    base = ROOT / "data/models/melo-es"
    for name in ("config.json", "checkpoint.pth"):
        if not (base / name).is_file():
            hf_hub_download("myshell-ai/MeloTTS-Spanish", name, local_dir=base)
    return folder, base


def main():
    preparing = sys.argv[1] == "--prepare"
    if not preparing and (ROOT / "data/models/openvoice-v2/ready.json").exists():
        os.environ["HF_HUB_OFFLINE"] = "1"
    request = (
        None if preparing else json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    )

    def status(done, message):
        if request:
            path = Path(request["progress"])
            temp = path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(
                    {"done": done, "total": len(request["items"]), "message": message}
                ),
                encoding="utf-8",
            )
            temp.replace(path)

    status(0, "Cargando OpenVoice V2 + MeloTTS español…")
    import torch
    import soundfile as sf
    import numpy as np

    torch.set_num_threads(4)
    folder, base = prepare_files()

    # Upstream imports every language at once. This worker only supports ES;
    # load its official phonemizer without loading Japanese/Korean/Chinese.
    spanish = importlib.import_module("melo.text.spanish")
    cleaner = types.ModuleType("melo.text.cleaner")

    def clean_text(text, language):
        if language != "ES":
            raise ValueError("Este motor rápido está configurado para español.")
        normalized = spanish.text_normalize(text)
        return (normalized, *spanish.g2p(normalized))

    cleaner.clean_text = clean_text
    sys.modules["melo.text.cleaner"] = cleaner
    from melo.api import TTS
    from openvoice.api import ToneColorConverter

    model = TTS(
        language="ES",
        device="cpu",
        config_path=str(base / "config.json"),
        ckpt_path=str(base / "checkpoint.pth"),
    )
    converter = ToneColorConverter(str(folder / "converter/config.json"), device="cpu")
    converter.load_ckpt(str(folder / "converter/checkpoint.pth"))
    # Bake the already trained normalization into convolution weights once.
    model.model.dec.remove_weight_norm()
    converter.model.dec.remove_weight_norm()
    source_se = torch.load(
        folder / "base_speakers/ses/es.pth", map_location="cpu", weights_only=True
    )
    parameters = sum(p.numel() for p in model.model.parameters()) + sum(
        p.numel() for p in converter.model.parameters()
    )
    print(f"OpenVoice V2 + Melo ES: {parameters:,} parámetros · CPU", flush=True)
    (folder / "ready.json").write_text(
        json.dumps({"ready": True, "parameters": parameters}), encoding="utf-8"
    )
    if preparing:
        return

    embeddings = ROOT / "data/cache/openvoice-speakers"
    embeddings.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(Path(request["reference"]).read_bytes()).hexdigest()
    embedding = embeddings / f"{key}.pt"
    status(0, "Preparando la identidad de voz OpenVoice…")
    target_se = (
        torch.load(embedding, map_location="cpu", weights_only=True)
        if embedding.exists()
        else converter.extract_se(request["reference"])
    )
    if not embedding.exists():
        torch.save(target_se.cpu(), embedding)
    metrics = []
    started = time.monotonic()
    for index, item in enumerate(request["items"]):
        status(
            index,
            f"OpenVoice: escena {index + 1}/{len(request['items'])} · generando español…",
        )
        torch.manual_seed(17)
        raw = Path(item["output"])
        neutral = raw.with_suffix(".base.wav")
        before = time.monotonic()
        if not neutral.exists():
            model.tts_to_file(
                item["text"], model.hps.data.spk2id["ES"], str(neutral), quiet=True
            )
        base_seconds = time.monotonic() - before
        status(
            index,
            f"OpenVoice: escena {index + 1}/{len(request['items'])} · aplicando voz clonada…",
        )
        before = time.monotonic()
        waveform = converter.convert(
            str(neutral), src_se=source_se, tgt_se=target_se, message="ImgV"
        )
        clone_seconds = time.monotonic() - before
        if not np.isfinite(waveform).all() or not len(waveform):
            raise ValueError("OpenVoice produjo audio inválido.")
        temp = raw.with_suffix(".part.wav")
        sf.write(temp, waveform, converter.hps.data.sampling_rate, subtype="PCM_16")
        temp.replace(raw)
        neutral.unlink(missing_ok=True)
        metrics.append(
            {
                "base_seconds": base_seconds,
                "clone_seconds": clone_seconds,
                "audio_seconds": len(waveform) / converter.hps.data.sampling_rate,
            }
        )
        Path(request["progress"]).with_name("openvoice-metrics.json").write_text(
            json.dumps({"parameters": parameters, "scenes": metrics}), encoding="utf-8"
        )
        remaining = (
            (time.monotonic() - started)
            / (index + 1)
            * (len(request["items"]) - index - 1)
        )
        status(
            index + 1,
            f"OpenVoice: {index + 1}/{len(request['items'])} terminadas · quedan ~{remaining / 60:.0f} min",
        )


if __name__ == "__main__":
    main()
