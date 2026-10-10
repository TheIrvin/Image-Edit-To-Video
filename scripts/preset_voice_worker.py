"""Spanish preset narrators, isolated from the voice cloning environments."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "data/models"
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def main():
    engine, argument = sys.argv[1:3]
    if engine not in {"supertonic", "scylla", "supertonic-clone", "kokoro"}:
        raise ValueError("Motor inválido")
    preparing = argument == "--prepare"
    request = {} if preparing else json.loads(Path(argument).read_text(encoding="utf-8"))
    def progress(done, message):
        print(message, flush=True)
        if request.get("progress"):
            path = Path(request["progress"])
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(dict(done=done, message=message)), encoding="utf-8")
            temporary.replace(path)
    progress(0, f"Cargando {engine} en CPU…")
    if engine in {"supertonic", "supertonic-clone"}:
        from supertonic import TTS
        model = TTS(model="supertonic-3", model_dir=MODELS / "supertonic3", auto_download=preparing,
                    intra_op_num_threads=4, inter_op_num_threads=1)
        speakers = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]
    elif engine == "kokoro":
        from kokoro_onnx import Kokoro
        directory = MODELS / "kokoro"
        directory.mkdir(parents=True, exist_ok=True)
        if preparing:
            import urllib.request
            for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
                path = directory / name
                if not path.exists():
                    progress(0, f"Descargando {name}…")
                    temporary = path.with_suffix(path.suffix + ".download")
                    urllib.request.urlretrieve("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/" + name, temporary)
                    temporary.replace(path)
        model = Kokoro(str(directory / "kokoro-v1.0.onnx"), str(directory / "voices-v1.0.bin"))
        # Runtime 0.4.9 sends int32 speed for newer exports, even when the
        # official graph declares float. Honor the graph's actual input types.
        import numpy as np
        class TypedSession:
            def __init__(self, session):
                self.session = session
            def get_inputs(self):
                return self.session.get_inputs()
            def run(self, names, inputs):
                dtypes = {"tensor(float)": np.float32, "tensor(int64)": np.int64, "tensor(int32)": np.int32}
                for entry in self.get_inputs():
                    if entry.name in inputs and entry.type in dtypes:
                        inputs[entry.name] = np.asarray(inputs[entry.name], dtype=dtypes[entry.type])
                return self.session.run(names, inputs)
        model.sess = TypedSession(model.sess)
        speakers = ["ef_dora", "em_alex", "em_santa"]
        if not set(speakers).issubset(model.get_voices()):
            raise ValueError("El paquete Kokoro no contiene las tres voces españolas")
    else:
        from scyllasband.download import download_bundle
        from scyllasband.runtime import ScyllasBandRuntime
        bundle = MODELS / "scyllasbandv2/onnx"
        if preparing and not (bundle / "manifest.json").exists():
            download_bundle(flavor="onnx", models_dir=bundle.parent,
                            repo_id="spybyscript/scyllasband", revision="v2-20261006-apple")
        model = ScyllasBandRuntime.from_bundle(bundle, backend="onnx", threads=4)
        speakers = model.available_voices()
    if preparing:
        import math
        import re
        import onnx
        directory = MODELS / {"supertonic": "supertonic3", "scylla": "scyllasbandv2/onnx", "kokoro": "kokoro"}[engine]
        graphs = []
        for path in directory.rglob("*.onnx"):
            if path.name == "speaker_encoder.onnx":
                continue  # Used only by the optional community voice fitter.
            # Scylla exports the same estimator and vocoder at several sequence
            # lengths. Count the canonical dynamic graph of each network once.
            if re.fullmatch(r"(?:vector_estimator|vocoder)_\d+", path.stem):
                continue
            graph = onnx.load(str(path), load_external_data=False).graph
            graphs.append(dict(graph=path.name, elements=sum(math.prod(w.dims) for w in graph.initializer)))
        parameters = sum(g["elements"] for g in graphs)
        if parameters >= 300_000_000:
            raise ValueError("El narrador completo excede el límite de 300M")
        marker = MODELS / f"{engine}-ready.json"
        marker.write_text(json.dumps(dict(speakers=speakers, backend="onnx", language="es", parameters=parameters,
                                          graph_counts=graphs, count_note=("ONNX initializers including vocoder; Spanish phonemization uses non-neural eSpeak NG" if engine == "kokoro" else "ONNX initializers; one canonical graph per network, includes vocoder and G2P"))), encoding="utf-8")
        return
    speaker = request.get("speaker")
    if engine != "supertonic-clone" and speaker not in speakers:
        raise ValueError("Voz predefinida inválida")
    import soundfile as sf
    for index, item in enumerate(request["items"]):
        output = Path(item["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".part.wav")
        progress(index, f"{index}/{len(request['items'])} · narrando en español…")
        if engine in {"supertonic", "supertonic-clone"}:
            audio, _ = model.synthesize(text=item["text"], voice_style=(model.get_voice_style_from_path(request["style_path"]) if engine == "supertonic-clone" else model.get_voice_style(voice_name=speaker)),
                                        total_steps=8, speed=1.0, lang="es", verbose=False)
            sf.write(str(temporary), audio.reshape(-1), model.sample_rate, subtype="PCM_16")
        elif engine == "kokoro":
            import numpy as np
            # Bound each utterance before phonemization; never truncate long scenes.
            from voice_worker import chunks
            parts = [model.create(part, voice=speaker, speed=1.0, lang="es")[0] for part in chunks(item["text"], limit=220)]
            sf.write(str(temporary), np.concatenate(parts), 24000, subtype="PCM_16")
        else:
            result = model.render_records([dict(text=item["text"], voice=speaker, language="es",
                                               delivery="energy=2.3,tension=2,valence=2.2,assertiveness=2.3")],
                                          steps=8, sampler="heun", seed=42)
            sf.write(str(temporary), result.audio.reshape(-1), result.sample_rate, subtype="PCM_16")
        temporary.replace(output)
        progress(index + 1, f"{index + 1}/{len(request['items'])} audios terminados")


if __name__ == "__main__":
    main()
