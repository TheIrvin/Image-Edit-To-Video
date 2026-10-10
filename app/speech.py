from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

from .core import DATA, ROOT, audio_key, ensure_data, identifier, read_json, write_json


def voice_python():
    return (
        ROOT
        / ".venv-voice"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )


def openvoice_status():
    acceleration = read_json(ROOT / "data/models/openvoice-v2/openvino/profile.json", {})
    return {
        "ready": voice_python().exists()
        and (ROOT / "data/models/openvoice-v2/ready.json").exists(),
        "model": "OpenVoice V2 + MeloTTS ES",
        "acceleration": acceleration,
    }


PRESET_ENGINES = {"supertonic": "Supertonic 3", "scylla": "Scylla’s Band v2", "kokoro": "Kokoro 82M"}


def narrator_python():
    return ROOT / ".venv-narrators" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def preset_engine_status(engine):
    profile = read_json(DATA / "models" / f"{engine}-ready.json", {})
    return {"ready": narrator_python().exists() and bool(profile), "profile": profile}


def install_preset_engine(engine, job):
    if engine not in PRESET_ENGINES:
        raise ValueError("Motor inválido")
    python = narrator_python()
    if not python.exists():
        job.run([sys.executable, "-m", "venv", str(python.parent.parent)])
    uv = shutil.which("uv")
    prefix = [uv, "pip", "install", "--python", str(python)] if uv else [str(python), "-m", "pip", "install"]
    job.update(progress=5, message="Preparando narradores ONNX…")
    job.run(prefix + (["kokoro-onnx==0.4.9", "soundfile", "onnx==1.23.2"] if engine == "kokoro" else ["supertonic==1.3.1", "onnx==1.23.2"]))
    if engine == "scylla":
        job.run(prefix + ["--no-deps", "https://github.com/lowkeytea/scyllasband/archive/a5b7ea118db89b6d4630a8d3aff98c77622fb54c.zip"])
    job.run([str(python), "-X", "utf8", str(ROOT / "scripts/preset_voice_worker.py"), engine, "--prepare"])
    register_preset_voices(engine)
    return {"engine": engine, "ready": True}


def register_preset_voices(engine):
    profile = preset_engine_status(engine)["profile"]
    for speaker in profile.get("speakers", []):
        voice_id = uuid.uuid5(uuid.NAMESPACE_URL, "local-narrator:" + engine + ":" + speaker.lower()).hex
        directory = DATA / "voices" / voice_id
        if (directory / "voice.json").exists() or (directory / "voice.archived.json").exists():
            continue
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / "voice.json", dict(id=voice_id, name=PRESET_ENGINES[engine] + " · " + speaker,
                   engine=engine, language="es", kind="Voz predefinida", speaker=speaker,
                   model_version={"supertonic": "supertonic-3-724fb5ab", "scylla": "scylla-v2-20261006-apple", "kokoro": "kokoro-onnx-v1.0"}[engine],
                   created=time.time(), preview=None))


def supertonic_clone_binary():
    return DATA / "tools/supertonic-clone/bin" / ("main.exe" if os.name == "nt" else "main")


def supertonic_clone_status():
    profile = read_json(DATA / "models/supertonic-clone-ready.json", {})
    return {"ready": supertonic_clone_binary().exists() and bool(profile), "profile": profile}


def fit_supertonic_voice(voice, directory, job, effort):
    import re
    budgets = {"fast": (6, 9, 15), "balanced": (12, 18, 30), "deep": (40, 60, 100)}
    if effort not in budgets:
        raise ValueError("Profundidad de clonación inválida")
    if not supertonic_clone_status()["ready"]:
        raise ValueError("El clonador comunitario Supertonic no está instalado.")
    mix, rows, free = budgets[effort]
    style = directory / "style.json"
    command = [str(supertonic_clone_binary()), "--root-models-path", str(DATA / "models/supertonic3"),
               "--device", "cpu", "clone", "--reference", voice["reference"], "--language", "es",
               "--output", str(style), "--mix-iters", str(mix), "--row-iters", str(rows), "--iters", str(free),
               "--voice-quality", "5", "--save-every", "10"]
    if voice.get("reference_text"):
        command += ["--ref-text", voice["reference_text"]]
    job.update(progress=20, message="Adaptando timbre y tono con Supertonic. Puede tardar varios minutos…")
    last_line = None
    def progress():
        nonlocal last_line
        log = DATA / "jobs" / f"{job.id}.log"
        lines = log.read_bytes()[-2500:].decode("utf-8", errors="replace").splitlines() if log.exists() else []
        if lines and lines[-1] != last_line:
            last_line = lines[-1]
            match = re.search(r"\[(select-preset|mix-presets|mix-rows|refine)\]\s+(\d+)/(\d+)", last_line)
            percent = 20
            if match:
                offsets = {"select-preset": 0, "mix-presets": 10, "mix-rows": 10 + mix, "refine": 10 + mix + rows}
                percent += 70 * (offsets[match[1]] + int(match[2])) / (10 + mix + rows + free)
            job.update(progress=percent, message="Clonación Supertonic · " + last_line[-200:])
    job.run(command, progress=progress)
    data = read_json(style)
    if not data.get("style_ttl") or not data.get("style_dp"):
        raise ValueError("El clonador no produjo un perfil de voz válido")
    voice.update(style_path=str(style), clone_effort=effort, model_version="supertonic3-clone-1.3.0",
                 kind="Voz adaptada · experimental", clone_metadata=data.get("metadata", {}))


def engine_status():
    marker = ROOT / "data/models/chatterbox-ready.json"
    return {
        "installed": voice_python().exists(),
        "ready": voice_python().exists() and marker.exists(),
        "model": "Chatterbox Multilingual V2",
        "device": "CPU",
        "cpu_profile": read_json(ROOT / "data/models/cpu-profile.json", {}),
        "openvoice": openvoice_status(),
        "supertonic-clone": supertonic_clone_status(),
        "moss": light_engine_status("moss"),
        **{engine: preset_engine_status(engine) for engine in PRESET_ENGINES},
        "note": "Español y clonación local. En CPU la síntesis puede tardar más que el audio resultante. Primera instalación: varios GB y conexión a Internet.",
    }


def system_voices():
    ensure_data()
    path = DATA / "cache" / "system-voices.json"
    if os.name != "nt":
        return []
    if not path.exists():
        process = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts/sapi.ps1"),
                "-ListPath",
                str(path),
            ],
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if process.returncode != 0:
            return []
    items = read_json(path, [])
    if isinstance(items, dict):
        items = [items]
    return [
        {
            "id": "system-" + str(i),
            "name": v["name"],
            "language": v["language"],
            "engine": "sapi",
            "system_name": v["name"],
            "kind": "Voz de Windows",
            "preview": None,
        }
        for i, v in enumerate(items)
        if v.get("enabled", True)
    ]


def list_voices():
    ensure_data()
    return (
        sorted(
            [read_json(p) for p in (DATA / "voices").glob("*/voice.json")],
            key=lambda v: v["created"],
            reverse=True,
        )
        + system_voices()
    )


def get_voice(voice_id):
    for voice in list_voices():
        if voice["id"] == voice_id:
            return voice
    raise ValueError("Selecciona una voz existente.")


def register_voice(name: str, reference: Path, ffmpeg: str, job, engine="openvoice", reference_text="", clone_effort="balanced"):
    if engine not in {"chatterbox", "openvoice", "moss", "supertonic-clone"}:
        raise ValueError("Motor de voz inválido.")
    if not name.strip() or len(name) > 100:
        raise ValueError("La voz necesita un nombre de hasta 100 caracteres.")
    if not reference.is_file():
        raise ValueError("Selecciona una muestra de audio válida.")
    voice_id = uuid.uuid4().hex
    directory = DATA / "voices" / voice_id
    directory.mkdir(parents=True)
    output = directory / "reference.wav"
    job.update(message="Preparando muestra de voz", progress=10)
    # Se conserva una muestra limpia, mono y limitada a 30 s.
    job.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(reference),
            "-t",
            "30",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    duration = wav_duration(output)
    if duration < 3:
        output.unlink(missing_ok=True)
        raise ValueError(
            "La muestra debe contener al menos 3 segundos. Recomiendo 8–15 s de voz limpia, sin música."
        )
    voice = {
        "id": voice_id,
        "name": name.strip(),
        "engine": engine,
        "language": "es",
        "kind": "Voz clonada",
        "created": time.time(),
        "reference": str(output),
        "sample_duration": duration,
        "preview": None,
        "preview_text": "",
        "reference_text": reference_text.strip(),
    }
    if engine == "supertonic-clone":
        fit_supertonic_voice(voice, directory, job, clone_effort)
    write_json(directory / "voice.json", voice)
    return {"voice_id": voice_id}


def wav_duration(path: Path):
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def compact_voice_pauses(source: Path, destination: Path):
    """Reducir huecos de síntesis y bordes vacíos sin acelerar la voz."""
    import numpy as np

    with wave.open(str(source), "rb") as wav:
        params = wav.getparams()
        if (params.nchannels, params.sampwidth) != (1, 2):
            raise ValueError("Formato inesperado al ajustar pausas.")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    window = max(1, round(params.framerate * 0.01))
    count = len(samples) // window
    if count:
        blocks = samples[:count * window].astype(np.float32).reshape(count, window)
        # La conversión de voz puede dejar ruido tenue en vez de ceros.
        # El pico protege ataques breves que el promedio de energía oculta.
        quiet = (
            (np.sqrt(np.mean(blocks * blocks, axis=1)) < 32768 * 10 ** (-42 / 20))
            & (np.max(np.abs(blocks), axis=1) < 32768 * 10 ** (-32 / 20))
        )
        edges = np.diff(np.r_[False, quiet, False].astype(np.int8))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        active = np.flatnonzero(~quiet)
        # Una salida sin voz detectable no se convierte en un WAV vacío.
        if not len(active):
            raise ValueError("No se detectó voz suficiente en el audio; revisa la muestra o regenera la escena.")
        leading = max(0, int(active[0]) * window - round(params.framerate * 0.06))
        trailing = min(len(samples), (int(active[-1]) + 1) * window + round(params.framerate * 0.10))
        pieces, cursor = [], leading
        for start, end in zip(starts, ends):
            # Conservar pausas normales; acortar sólo huecos de más de 600 ms.
            if start == 0 or end == count or (end - start) * window < params.framerate * 0.60:
                continue
            first, last = start * window, end * window
            keep = round(params.framerate * 0.32)
            cut_start = first + keep // 2
            cut_end = last - (keep - keep // 2)
            pieces.append(samples[cursor:cut_start])
            cursor = cut_end
        pieces.append(samples[cursor:trailing])
        samples = np.concatenate(pieces)
    temp = destination.with_suffix(".part.wav")
    with wave.open(str(temp), "wb") as wav:
        wav.setparams(params)
        wav.writeframes(samples.tobytes())
    temp.replace(destination)


def synthesize(items, voice, speed, job, ffmpeg, start=0, span=45):
    import hashlib

    originals = _synthesize(items, voice, speed, job, ffmpeg, start, span)
    if voice["engine"] not in {"chatterbox", "openvoice", "moss"}:
        return originals
    outputs = []
    for original in originals:
        job.check()
        # Versionar sólo la edición: se reutiliza la síntesis original en caché.
        key = hashlib.sha256((original.stem + ":paced-v2").encode()).hexdigest()
        edited = original.with_name(key + ".wav")
        if not edited.exists():
            compact_voice_pauses(original, edited)
        outputs.append(edited)
    return outputs


def _synthesize(
    items: list[dict], voice: dict, speed: float, job, ffmpeg: str, start=0, span=45
):
    ensure_data()
    cache = DATA / "cache/audio"
    cache.mkdir(parents=True, exist_ok=True)
    outputs, missing = [], []
    pending_keys = set()
    for item in items:
        key = audio_key(item["text"], voice, speed)
        path = cache / f"{key}.wav"
        outputs.append(path)
        if not path.exists() and key not in pending_keys:
            pending_keys.add(key)
            missing.append(
                {
                    "text": item["text"],
                    "output": str(cache / f"{key}.raw.wav"),
                    "final": str(path),
                }
            )
    if not missing:
        job.update(progress=start + span, message="Audio recuperado de la caché")
        return outputs
    if float(speed) != 1.0:
        # La velocidad es edición del WAV; nunca obliga a clonar la voz de nuevo.
        originals = _synthesize(
            missing, voice, 1.0, job, ffmpeg, start=start, span=span * 0.9
        )
        for index, (item, original) in enumerate(zip(missing, originals)):
            job.check()
            final = Path(item["final"])
            temp = final.with_suffix(".part.wav")
            job.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(original),
                    "-af",
                    f"atempo={speed}",
                    "-ar",
                    "24000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(temp),
                ]
            )
            if wav_duration(temp) <= 0:
                raise ValueError("La edición de velocidad produjo audio vacío.")
            temp.replace(final)
            job.update(
                progress=start + span * (0.9 + 0.1 * (index + 1) / len(missing)),
                message="Ajustando velocidad del audio guardado…",
            )
        return outputs
    directory = DATA / "jobs" / job.id
    directory.mkdir(parents=True, exist_ok=True)
    progress_path = directory / "speech-progress.json"
    # A job can synthesize several voices in sequence. Progress from the
    # preceding batch must not claim that the next batch's file already exists.
    progress_path.unlink(missing_ok=True)
    request = {
        "items": missing,
        "progress": str(progress_path),
        "language": voice["language"],
    }
    if voice["engine"] == "supertonic-clone":
        if not supertonic_clone_status()["ready"]:
            raise ValueError("El clonador Supertonic no está instalado")
        request["style_path"] = voice["style_path"]
        command = [str(narrator_python()), "-X", "utf8", str(ROOT / "scripts/preset_voice_worker.py"), "supertonic-clone"]
    elif voice["engine"] in PRESET_ENGINES:
        if not preset_engine_status(voice["engine"])["ready"]:
            raise ValueError("Instala este narrador desde Biblioteca de voces.")
        request["speaker"] = voice["speaker"]
        command = [str(narrator_python()), "-X", "utf8", str(ROOT / "scripts/preset_voice_worker.py"), voice["engine"]]
    elif voice["engine"] in {"chatterbox", "openvoice", "moss"}:
        if voice["engine"] in {"moss"}:
            engine = voice["engine"]
            if not light_engine_status(engine)["ready"]:
                raise ValueError("Instala este motor desde Biblioteca de voces.")
            request["reference"] = voice["reference"]
            request["reference_text"] = voice.get("reference_text", "")
            command = [str(light_voice_python(engine)), "-X", "utf8"]
            command += [str(ROOT / "scripts/light_voice_worker.py"), engine]
        elif voice["engine"] == "openvoice":
            if not openvoice_status()["ready"]:
                raise ValueError("Instala OpenVoice V2 desde Biblioteca de voces.")
            request["reference"] = voice["reference"]
            command = [
                str(voice_python()),
                "-X",
                "utf8",
                str(ROOT / "scripts/openvoice_worker.py"),
            ]
        else:
            if not engine_status()["ready"]:
                raise ValueError(
                    "Instala Chatterbox desde Voces antes de usar esta voz."
                )
            request["reference"] = voice["reference"]
            command = [str(voice_python()), str(ROOT / "scripts/voice_worker.py")]
    else:
        request["voice"] = voice["system_name"]
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts/sapi.ps1"),
            "-RequestPath",
        ]
    request_path = directory / "speech-request.json"
    write_json(request_path, request)
    job.update(
        progress=start + span * sum(path.exists() for path in outputs) / len(outputs),
        message=f"{sum(path.exists() for path in outputs)}/{len(outputs)} audios en caché · cargando voz para los pendientes…",
    )
    last_done = None

    def normalize(item):
        raw, path = Path(item["output"]), Path(item["final"])
        if path.exists():
            return
        temp = path.with_suffix(".part.wav")
        with wave.open(str(raw), "rb") as source:
            ready = (
                source.getnchannels(),
                source.getsampwidth(),
                source.getframerate(),
                source.getcomptype(),
            ) == (1, 2, 24000, "NONE")
        if float(speed) == 1.0 and ready:
            if wav_duration(raw) <= 0:
                raise ValueError("El sintetizador produjo un archivo vacío.")
            raw.replace(path)
            return
        # Normalizar primero; el ajuste de pausas se aplica después en synthesize.
        job.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-i",
                str(raw),
                "-af",
                f"atempo={speed}",
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(temp),
            ]
        )
        if wav_duration(temp) <= 0:
            raise ValueError("El sintetizador produjo un archivo vacío.")
        temp.replace(path)
        raw.unlink(missing_ok=True)

    completed = 0

    def progress():
        nonlocal last_done, completed
        try:
            value = json.loads(progress_path.read_text(encoding="utf-8"))
            done = max(0, min(int(value["done"]), len(missing)))
            message = value["message"]
        except (OSError, ValueError, KeyError):
            return
        # Guardar cada audio terminado mientras el modelo narra el siguiente.
        # Si se cancela, estas escenas completas sí se reutilizan al reintentar.
        for index in range(completed, done):
            normalize(missing[index])
        completed = max(completed, done)
        identity = (done, message)
        if identity != last_done:
            last_done = identity
            job.update(
                progress=start
                + span * sum(path.exists() for path in outputs) / len(outputs),
                message=message,
            )

    job.run(command + [str(request_path)], progress=progress)
    for item in missing:
        normalize(item)
    return outputs


def preview_voice(voice_id, text, job, ffmpeg):
    voice = get_voice(voice_id)
    text = (
        text.strip()
        or "La noche había caído sobre la ciudad. Nadie imaginaba que aquella decisión cambiaría su historia para siempre."
    )
    if len(text) > 700:
        raise ValueError("El ejemplo puede tener hasta 700 caracteres.")
    generation_started = time.perf_counter()
    path = synthesize([{"text": text}], voice, 1, job, ffmpeg, span=90)[0]
    generation_seconds = round(time.perf_counter() - generation_started, 2)
    if voice["engine"] in {"chatterbox", "openvoice", "moss", "supertonic-clone", *PRESET_ENGINES}:
        directory = DATA / "voices" / identifier(voice_id)
        preview = directory / "preview.wav"
        shutil.copy2(path, preview)
        voice.update(preview=f"/api/voices/{voice_id}/preview", preview_text=text, preview_generation_seconds=generation_seconds, preview_duration=round(wav_duration(path), 2))
        write_json(directory / "voice.json", voice)
        url = voice["preview"]
    else:
        url = f"/api/audio/{path.stem}"
    return {"audio_url": url, "voice_id": voice_id}


def install_engine(job):
    python = voice_python()
    if not python.exists():
        job.update(progress=5, message="Creando entorno separado para las voces…")
        job.run([sys.executable, "-m", "venv", str(ROOT / ".venv-voice")])
    uv = shutil.which("uv")
    prefix = (
        [uv, "pip", "install", "--python", str(python)]
        if uv
        else [str(python), "-m", "pip", "install"]
    )
    job.update(progress=10, message="Instalando PyTorch para CPU…")
    job.run(
        prefix
        + [
            "torch==2.6.0",
            "torchaudio==2.6.0",
            "--index-url",
            "https://download.pytorch.org/whl/cpu",
        ]
    )
    job.update(progress=30, message="Instalando Chatterbox Multilingual…")
    job.run(prefix + ["chatterbox-tts==0.1.7"])
    job.update(
        progress=55, message="Descargando y comprobando el modelo de voz (varios GB)…"
    )
    job.run([str(python), str(ROOT / "scripts/voice_worker.py"), "--prepare"])
    return engine_status()


def optimize_cpu(job):
    if not engine_status()["ready"]:
        raise ValueError("Instala el motor de voz primero.")
    job.update(
        progress=5,
        message="Comparando CPU original e INT8; esta medición tarda unos minutos…",
    )
    job.run([str(voice_python()), str(ROOT / "scripts/benchmark_voice_cpu.py")])
    return engine_status()


def install_openvoice(job):
    python = voice_python()
    if not python.exists():
        job.run([sys.executable, "-m", "venv", str(ROOT / ".venv-voice")])
    uv = shutil.which("uv")
    prefix = (
        [uv, "pip", "install", "--python", str(python)]
        if uv
        else [str(python), "-m", "pip", "install"]
    )
    job.update(progress=5, message="Instalando motor rápido de clonación para CPU…")
    job.run(
        prefix
        + [
            "torch==2.6.0",
            "torchaudio==2.6.0",
            "--index-url",
            "https://download.pytorch.org/whl/cpu",
        ]
    )
    job.run(
        prefix
        + [
            "--no-deps",
            "git+https://github.com/myshell-ai/OpenVoice.git@74a1d147b17a8c3092dd5430504bd83ef6c7eb23",
            "git+https://github.com/myshell-ai/MeloTTS.git@209145371cff8fc3bd60d7be902ea69cbdb7965a",
        ]
    )
    job.run(
        prefix
        + [
            "numpy>=1.26,<2",
            "librosa>=0.11,<0.12",
            "soundfile>=0.13,<1",
            "transformers>=4.46,<5",
            "huggingface-hub<1",
            "txtsplit",
            "cached-path",
            "num2words",
            "gruut[es]==2.2.3",
            "eng-to-ipa",
            "inflect>=7,<8",
            "unidecode",
            "pypinyin",
            "cn2an",
            "jieba",
            "wavmark",
            "tqdm",
        ]
    )
    job.update(progress=60, message="Descargando MeloTTS español y OpenVoice V2…")
    job.run(
        [
            str(python),
            "-X",
            "utf8",
            str(ROOT / "scripts/openvoice_worker.py"),
            "--prepare",
        ]
    )
    return openvoice_status()


def optimize_openvoice(job):
    if not openvoice_status()["ready"]:
        raise ValueError("Instala OpenVoice primero.")
    python = voice_python()
    uv = shutil.which("uv")
    prefix = [uv, "pip", "install", "--python", str(python)] if uv else [str(python), "-m", "pip", "install"]
    job.update(progress=5, message="Instalando aceleración OpenVINO…")
    job.run(prefix + ["openvino>=2025,<2027"])
    job.update(progress=20, message="Preparando modelos OpenVINO; se hace una sola vez…")
    job.run([str(python), "-X", "utf8", str(ROOT / "scripts/openvoice_worker.py"), "--prepare-openvino"])
    voices = [v for v in list_voices() if v["engine"] == "openvoice"]
    if not voices:
        raise ValueError("Guarda una voz OpenVoice para medir la aceleración.")
    directory = DATA / "jobs" / job.id
    directory.mkdir(parents=True, exist_ok=True)
    request = directory / "openvino-benchmark-request.json"
    write_json(request, {
        "reference": voices[0]["reference"], "benchmark_openvino": True,
        "progress": str(directory / "speech-progress.json"),
        "items": [{"text": "Nico abrió los ojos. Nadie había escuchado la puerta."},
                  {"text": "Durante los días siguientes, Nico regresó al puente para comprender lo sucedido. Recordaba la mano del desconocido, el agua ocupando el pasillo y la salida que habían conseguido abrir. Ahora miraba la ciudad en silencio, preguntándose quién volvería a casa aquella noche."}],
    })
    job.update(progress=60, message="Comparando PyTorch y OpenVINO CPU con narraciones reales…")
    job.run([str(python), "-X", "utf8", str(ROOT / "scripts/openvoice_worker.py"), str(request)])
    return openvoice_status()


LIGHT_ENGINES = {"moss": "MOSS-TTS-Nano ONNX"}
MOSS_REVISION = "8b7bcc9341b3b4ef3a3a58ba1338a7d85ff133eb"


def light_voice_python(engine=None):
    return ROOT / ".venv-tts-cpu" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def light_engine_status(engine):
    if engine not in LIGHT_ENGINES:
        raise ValueError("Motor de voz inválido.")
    marker = read_json(DATA / f"models/{engine}-ready.json", {})
    python = light_voice_python(engine)
    return {"ready": python.exists() and bool(marker), "model": LIGHT_ENGINES[engine], "device": "CPU", "profile": marker, "installed": python.exists(), "access_required": (DATA / f"models/{engine}-access-required.json").exists()}


def install_light_engine(engine, job):
    if engine not in LIGHT_ENGINES:
        raise ValueError("Motor de voz inválido.")
    python = light_voice_python(engine)
    if not python.exists():
        job.update(progress=3, message="Creando entorno separado para motores ligeros…")
        job.run([sys.executable, "-m", "venv", str(ROOT / ".venv-tts-cpu")])
    uv = shutil.which("uv")
    prefix = [uv, "pip", "install", "--python", str(python)] if uv else [str(python), "-m", "pip", "install"]
    job.update(progress=10, message="Instalando dependencias para CPU…")
    job.run(prefix + ["torch==2.7.0", "torchaudio==2.7.0", "--index-url", "https://download.pytorch.org/whl/cpu"])
    job.run(prefix + ["soundfile>=0.13,<1", "onnxruntime>=1.20,<2", "sentencepiece>=0.2.1", "huggingface-hub>=0.34,<1", "librosa>=0.11,<0.12"])
    if engine == "moss":
        job.update(progress=35, message="Preparando código oficial MOSS ONNX…")
        import urllib.request
        import zipfile
        import io
        source = DATA / "models/moss-source"
        marker = source / "source-revision.txt"
        if not marker.exists() or marker.read_text().strip() != MOSS_REVISION:
            with urllib.request.urlopen(f"https://codeload.github.com/OpenMOSS/MOSS-TTS-Nano/zip/{MOSS_REVISION}", timeout=120) as response:
                archive = zipfile.ZipFile(io.BytesIO(response.read()))
            for entry in archive.infolist():
                relative = Path(*Path(entry.filename).parts[1:])
                if not relative.parts or ".." in relative.parts:
                    continue
                destination = source / relative
                if entry.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(entry))
            marker.write_text(MOSS_REVISION)
    job.update(progress=55, message=f"Descargando y cargando {LIGHT_ENGINES[engine]}…")
    job.run([str(python), "-X", "utf8", str(ROOT / "scripts/light_voice_worker.py"), engine, "--prepare"])
    return light_engine_status(engine)
