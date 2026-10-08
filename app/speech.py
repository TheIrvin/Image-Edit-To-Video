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
    return {
        "ready": voice_python().exists()
        and (ROOT / "data/models/openvoice-v2/ready.json").exists(),
        "model": "OpenVoice V2 + MeloTTS ES",
    }


def engine_status():
    marker = ROOT / "data/models/chatterbox-ready.json"
    return {
        "installed": voice_python().exists(),
        "ready": voice_python().exists() and marker.exists(),
        "model": "Chatterbox Multilingual V2",
        "device": "CPU",
        "cpu_profile": read_json(ROOT / "data/models/cpu-profile.json", {}),
        "openvoice": openvoice_status(),
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


def register_voice(name: str, reference: Path, ffmpeg: str, job, engine="chatterbox"):
    if engine not in {"chatterbox", "openvoice"}:
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
    }
    write_json(directory / "voice.json", voice)
    return {"voice_id": voice_id}


def wav_duration(path: Path):
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def compact_voice_pauses(source: Path, destination: Path):
    """Acortar sólo silencios interiores prolongados en PCM, sin regenerar voz."""
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
        quiet = np.sqrt(np.mean(blocks * blocks, axis=1)) < 32768 * 10 ** (-48 / 20)
        edges = np.diff(np.r_[False, quiet, False].astype(np.int8))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        pieces, cursor = [], 0
        for start, end in zip(starts, ends):
            # No tocar entrada/salida ni pausas normales ni fonemas breves.
            if start == 0 or end == count or (end - start) * window < params.framerate * 0.65:
                continue
            first, last = start * window, end * window
            keep = round(params.framerate * 0.35)
            cut_start = first + keep // 2
            cut_end = last - (keep - keep // 2)
            pieces.append(samples[cursor:cut_start])
            cursor = cut_end
        pieces.append(samples[cursor:])
        samples = np.concatenate(pieces)
    temp = destination.with_suffix(".part.wav")
    with wave.open(str(temp), "wb") as wav:
        wav.setparams(params)
        wav.writeframes(samples.tobytes())
    temp.replace(destination)


def synthesize(items, voice, speed, job, ffmpeg, start=0, span=45):
    import hashlib

    originals = _synthesize(items, voice, speed, job, ffmpeg, start, span)
    if voice["engine"] not in {"chatterbox", "openvoice"}:
        return originals
    outputs = []
    for original in originals:
        job.check()
        key = hashlib.sha256((original.stem + ":paced-v1").encode()).hexdigest()
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
    request = {
        "items": missing,
        "progress": str(progress_path),
        "language": voice["language"],
    }
    if voice["engine"] in {"chatterbox", "openvoice"}:
        if voice["engine"] == "openvoice":
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
        # No silenceremove: nunca se corta la narración ni sus pausas originales.
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
    path = synthesize([{"text": text}], voice, 1, job, ffmpeg, span=90)[0]
    if voice["engine"] in {"chatterbox", "openvoice"}:
        directory = DATA / "voices" / identifier(voice_id)
        preview = directory / "preview.wav"
        shutil.copy2(path, preview)
        voice.update(preview=f"/api/voices/{voice_id}/preview", preview_text=text)
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
