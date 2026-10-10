"""Install the optional community voice fitter; reuse the existing TTS weights."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def main():
    cargo = shutil.which("cargo")
    if not cargo:
        raise RuntimeError("Instala Rust con Cargo antes de preparar el clonador comunitario.")
    models = ROOT / "data/models/supertonic3"
    if not (models / "onnx/vector_estimator.onnx").exists():
        raise RuntimeError("Instala Supertonic 3 desde Biblioteca de voces primero.")
    subprocess.run([cargo, "install", "supertonic3-tts", "--version", "1.3.0", "--locked", "--root",
                    str(ROOT / "data/tools/supertonic-clone")], check=True)
    speaker = models / "speaker_encoder.onnx"
    if not speaker.exists():
        temporary = speaker.with_suffix(".download")
        urllib.request.urlretrieve("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx", temporary)
        temporary.replace(speaker)
    import onnx
    counts = {p.name: sum(math.prod(t.dims) for t in onnx.load(str(p), load_external_data=False).graph.initializer)
              for p in [*models.glob("onnx/*.onnx"), speaker]}
    if sum(counts.values()) >= 300_000_000:
        raise RuntimeError("Los modelos exceden el límite de 300M.")
    marker = ROOT / "data/models/supertonic-clone-ready.json"
    marker.write_text(json.dumps(dict(parameters=sum(counts.values()), graph_counts=counts,
                                      version="supertonic3-tts 1.3.0", backend="CPU"), indent=2), encoding="utf-8")
    print("Clonador comunitario preparado:", sum(counts.values()), "elementos de pesos")


if __name__ == "__main__":
    main()
