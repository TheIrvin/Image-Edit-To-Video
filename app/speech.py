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


def engine_status():
    marker = ROOT / "data/models/chatterbox-ready.json"
    return {
        "installed": voice_python().exists(),
        "ready": voice_python().exists() and marker.exists(),
        "model": "Chatterbox Multilingual V2",
        "device": "CPU",
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


def register_voice(name: str, reference: Path, ffmpeg: str, job):
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
        "engine": "chatterbox",
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


def synthesize(
    items: list[dict], voice: dict, speed: float, job, ffmpeg: str, start=0, span=45
):
    ensure_data()
    cache = DATA / "cache/audio"
    cache.mkdir(parents=True, exist_ok=True)
    outputs, missing = [], []
    for item in items:
        key = audio_key(item["text"], voice, speed)
        path = cache / f"{key}.wav"
        outputs.append(path)
        if not path.exists():
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
    directory = DATA / "jobs" / job.id
    directory.mkdir(parents=True, exist_ok=True)
    progress_path = directory / "speech-progress.json"
    request = {
        "items": missing,
        "progress": str(progress_path),
        "language": voice["language"],
    }
    if voice["engine"] == "chatterbox":
        if not engine_status()["ready"]:
            raise ValueError(
                "Instala el motor Chatterbox desde Voces antes de usar una voz clonada."
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
        progress=start + 1,
        message="Cargando voz y narrando; el texto se conserva completo…",
    )
    last_done = None

    def progress():
        nonlocal last_done
        try:
            value = json.loads(progress_path.read_text(encoding="utf-8"))
            identity = (value["done"], value["message"])
            if identity != last_done:
                last_done = identity
                job.update(
                    progress=start + span * value["done"] / len(missing),
                    message=value["message"],
                )
        except (OSError, ValueError, KeyError):
            pass

    job.run(command + [str(request_path)], progress=progress)
    for item in missing:
        raw, path = Path(item["output"]), Path(item["final"])
        temp = path.with_suffix(".part.wav")
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
    if voice["engine"] == "chatterbox":
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
