"""CPU cloning backends; load once per batch and cache reference conditioning."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "data/models"
credential_home = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface")))
os.environ.setdefault("HF_TOKEN_PATH", str(credential_home / "token"))
os.environ.setdefault("HF_HOME", str(MODELS))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.1)


def main():
    engine, argument = sys.argv[1:3]
    if engine not in {"moss"}:
        raise ValueError("Motor no permitido")
    preparing = argument == "--prepare"
    request = {} if preparing else json.loads(Path(argument).read_text(encoding="utf-8"))
    def progress(done, message):
        print(message, flush=True)
        if request.get("progress"):
            write_json(request["progress"], {"done": done, "total": len(request["items"]), "message": message})
    progress(0, f"Cargando {engine} en CPU…")
    import torch
    torch.set_num_threads(4)
    source = MODELS / "moss-source"
    sys.path.insert(0, str(source))
    from onnx_tts_runtime import OnnxTtsRuntime
    if preparing:
        from huggingface_hub import snapshot_download
        for name in ["MOSS-TTS-Nano-100M-ONNX", "MOSS-Audio-Tokenizer-Nano-ONNX"]:
            for attempt in range(3):
                try:
                    progress(0, f"Descargando {name} (intento {attempt + 1}/3)…")
                    snapshot_download(repo_id=f"OpenMOSS-Team/{name}", local_dir=str(source / "models" / name), allow_patterns=["*.onnx", "*.data", "*.json", "tokenizer.model"], max_workers=2)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 * (attempt + 1))
    model = OnnxTtsRuntime(thread_count=4, execution_provider="cpu", output_dir=ROOT / "data/cache/moss")
    if not preparing:
        reference = Path(request["reference"])
        digest = hashlib.sha256(reference.read_bytes() + b"moss-8b7bcc93").hexdigest()
        cache = reference.parent / f"moss-{digest}.json"
        if cache.exists():
            codes = json.loads(cache.read_text(encoding="utf-8"))["codes"]
        else:
            progress(0, "Preparando y guardando perfil MOSS de esta voz…")
            codes = model.encode_reference_audio(str(reference))
            write_json(cache, {"codes": codes})
        # The official synthesize entrypoint requests the codes again per scene.
        # Reuse the trusted local conditioning instead of encoding each time.
        model.resolve_prompt_audio_codes = lambda **kwargs: codes
    if preparing:
        (MODELS / f"{engine}-access-required.json").unlink(missing_ok=True)
        write_json(MODELS / f"{engine}-ready.json", {"engine": engine, "language": "es", "device": "cpu", "prepared": time.time()})
        return
    import soundfile as sf
    import numpy as np
    for index, item in enumerate(request["items"]):
        progress(index, f"{engine}: narrando escena {index + 1}/{len(request['items'])}…")
        output = Path(item["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".tmp.wav")
        model.synthesize(text=item["text"], prompt_audio_path=str(reference), output_audio_path=str(temporary), streaming=True, sample_mode="fixed", do_sample=True, max_new_frames=750, voice_clone_max_text_tokens=75, enable_wetext=False, enable_normalize_tts_text=False)
        temporary.replace(output)
        progress(index + 1, "Escena narrada y guardada")


if __name__ == "__main__":
    main()
