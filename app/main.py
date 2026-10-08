from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import core, jobs, render, speech


@asynccontextmanager
async def lifespan(app):
    jobs.recover_jobs()
    yield


app = FastAPI(title="Imagen a Historia", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.middleware("http")
async def same_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
        parsed = urlparse(origin)
        if parsed.netloc != request.headers.get("host") or parsed.scheme not in {
            "http",
            "https",
        }:
            return JSONResponse(
                {"detail": "Solo se aceptan acciones desde esta app local."},
                status_code=403,
            )
    return await call_next(request)


@app.exception_handler(ValueError)
async def bad_request(request, error):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.exception_handler(FileNotFoundError)
async def not_found(request, error):
    return JSONResponse({"detail": str(error)}, status_code=404)


def file_response(path, **kwargs):
    if not path.is_file():
        raise FileNotFoundError("El archivo todavía no existe.")
    return FileResponse(path, **kwargs)


@app.get("/")
def index():
    return FileResponse(core.ROOT / "web/index.html")


@app.get("/api/status")
def status():
    memory = None
    if os.name == "nt":
        import ctypes

        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total",
                    "avail",
                    "total_page",
                    "avail_page",
                    "total_virtual",
                    "avail_virtual",
                    "extended",
                )
            ]

        value = Memory()
        value.length = ctypes.sizeof(value)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value))
        memory = round(value.total / 1024**3, 1)
    return {
        "application": "imagen-a-historia",
        "version": "1.0.0",
        "engine": speech.engine_status(),
        "ffmpeg": bool(render.ffmpeg_path()),
        "cpu_threads": os.cpu_count(),
        "ram_gb": memory,
        "platform": platform.system(),
        "data_directory": str(core.DATA),
    }


class PickRequest(BaseModel):
    kind: str


@app.post("/api/pick")
def pick(body: PickRequest):
    if body.kind not in {"folder", "script", "voice"}:
        raise ValueError("Tipo de selección inválido.")
    command = [sys.executable, str(core.ROOT / "scripts/picker.py"), body.kind]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0:
        raise ValueError(
            "No se pudo abrir el selector. Puedes pegar la ruta en el campo correspondiente."
        )
    return json.loads(result.stdout)


class ProjectImport(BaseModel):
    folder: str
    script: str
    name: str = ""
    pairing: str = "order"


@app.post("/api/projects")
def import_project(body: ProjectImport):
    return core.create_project(
        Path(body.folder), Path(body.script), body.name, body.pairing
    )


@app.get("/api/projects")
def projects():
    core.ensure_data()
    items = [core.read_json(p) for p in (core.DATA / "projects").glob("*/project.json")]
    return [
        {k: p[k] for k in ("id", "name", "created", "updated", "revision")}
        | {"scenes": len(p["scenes"])}
        for p in sorted(items, key=lambda p: p["updated"], reverse=True)
        if not p.get("cancelled")
    ]


@app.get("/api/projects/{project_id}")
def project(project_id: str):
    return core.get_project(project_id)


@app.post("/api/projects/{project_id}/cancel")
def cancel_project(project_id: str):
    with core.LOCK:
        current = core.get_project(project_id)
        current["cancelled"] = True
        core.write_json(core.DATA / "projects" / project_id / "project.json", current)
        for path in (core.DATA / "jobs").glob("*.json"):
            task = core.read_json(path)
            if task.get("project_id") == project_id and task.get("status") in {
                "queued",
                "running",
            }:
                jobs.EVENTS.setdefault(task["id"], threading.Event()).set()
    return {"ok": True}


class ProjectUpdate(BaseModel):
    revision: int
    name: str
    settings: dict
    scenes: list[dict]


@app.put("/api/projects/{project_id}")
def save_project(project_id: str, body: ProjectUpdate):
    import time

    with core.LOCK:
        current = core.get_project(project_id)
        if current["revision"] != body.revision:
            raise HTTPException(
                409,
                "El proyecto cambió en otra ventana. Vuelve a abrirlo antes de guardar.",
            )
        if len(body.scenes) != len(current["scenes"]):
            raise ValueError(
                "No se pueden quitar escenas al guardar; importa otro TXT para cambiar la estructura."
            )
        settings = current["settings"]
        for key in settings:
            if key in body.settings:
                settings[key] = body.settings[key]
        editable = (
            "text",
            "motion",
            "transition",
            "filter",
            "focus_x",
            "focus_y",
            "fit",
            "extra_pause",
        )
        by_number = {s.get("number"): s for s in body.scenes}
        if set(by_number) != {s["number"] for s in current["scenes"]}:
            raise ValueError("Los números de escena no coinciden.")
        for scene in current["scenes"]:
            for key in editable:
                if key in by_number[scene["number"]]:
                    scene[key] = by_number[scene["number"]][key]
        # El número identifica la pareja original; la lista define el montaje.
        originals = {scene["number"]: scene for scene in current["scenes"]}
        current["scenes"] = [originals[scene["number"]] for scene in body.scenes]
        current["name"] = body.name.strip()[:120] or current["name"]
        core.validate_project(current)
        current.update(revision=current["revision"] + 1, updated=time.time())
        core.write_json(core.DATA / "projects" / project_id / "project.json", current)
        return current


@app.get("/api/projects/{project_id}/scenes/{number}/image")
def scene_image(
    project_id: str, number: int, aspect: str = "original", revision: int = 0
):
    from PIL import Image, ImageOps

    p = core.get_project(project_id)
    s = next((s for s in p["scenes"] if s["number"] == number), None)
    if s is None:
        raise FileNotFoundError("Escena inexistente.")
    source = core.DATA / "projects" / project_id / "images" / s["image"]
    thumb_settings = (
        {}
        if aspect == "original"
        else {key: s[key] for key in ("filter", "focus_x", "focus_y", "fit")}
        | {"global_fit": p["settings"]["fit"]}
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "project": project_id,
                "image": s["image"],
                "settings": thumb_settings,
                "aspect": aspect,
                "layout_version": 2,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    dest = core.DATA / "cache" / f"thumb-{fingerprint}.jpg"
    if not dest.exists():
        if aspect == "original":
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((960, 960))
                image.save(dest, quality=85)
        elif aspect in {"16:9", "9:16"}:
            settings = dict(p["settings"], aspect=aspect)
            w, h = (640, 360) if aspect == "16:9" else (360, 640)
            render.prepare_image(source, dest, w, h, s, settings, overscan=False)
        else:
            raise ValueError("Formato de vista inválido.")
    return FileResponse(dest, media_type="image/jpeg")


class RenderRequest(BaseModel):
    preview: bool = False
    scene: int | None = None
    audio_only: bool = False
    voice_id: str | None = None


@app.post("/api/projects/{project_id}/render")
def generate(project_id: str, body: RenderRequest):
    p = core.get_project(project_id)
    if body.voice_id:
        if not body.preview and body.scene is None:
            raise ValueError(
                "La voz alternativa solo se puede probar en una vista previa."
            )
        p["settings"]["voice_id"] = body.voice_id
    core.validate_project(p)
    speech.get_voice(p["settings"]["voice_id"])
    label = (
        "Medir narración"
        if body.audio_only
        else "Vista previa"
        if body.preview or body.scene
        else "Exportar video"
    )
    return jobs.submit(
        "audio" if body.audio_only else "render",
        f"{label}: {p['name']}",
        lambda job: render.render(
            p,
            job,
            preview=body.preview,
            only_scene=body.scene,
            reuse_audio_only=body.audio_only,
        ),
        project_id=project_id,
    )


@app.get("/api/jobs")
def job_list():
    core.ensure_data()
    return sorted(
        [core.read_json(p) for p in (core.DATA / "jobs").glob("*.json")],
        key=lambda j: j["created"],
        reverse=True,
    )[:100]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = core.read_json(core.DATA / "jobs" / f"{core.identifier(job_id)}.json")
    if j is None:
        raise FileNotFoundError("Tarea inexistente.")
    return j


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    core.identifier(job_id)
    if job_id in jobs.EVENTS:
        jobs.EVENTS[job_id].set()
    return {"ok": True}


@app.get("/api/voices")
def voices():
    return speech.list_voices()


class VoiceImport(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    reference: str
    engine: str = "chatterbox"


@app.post("/api/voices")
def import_voice(body: VoiceImport):
    return jobs.submit(
        "voice",
        f"Guardar voz: {body.name}",
        lambda job: speech.register_voice(
            body.name, Path(body.reference), render.ffmpeg_path(), job, body.engine
        ),
    )


class VoicePreview(BaseModel):
    text: str = Field(default="", max_length=700)


@app.post("/api/voices/{voice_id}/openvoice")
def try_openvoice(voice_id: str, body: VoicePreview):
    original = speech.get_voice(voice_id)
    if original["engine"] != "chatterbox":
        raise ValueError("Selecciona una voz clonada de Chatterbox.")

    def prepare(job):
        existing = next(
            (
                voice
                for voice in speech.list_voices()
                if voice.get("source_voice_id") == voice_id
                and voice["engine"] == "openvoice"
            ),
            None,
        )
        if existing:
            new_id = existing["id"]
        else:
            result = speech.register_voice(
                (original["name"] + " · OpenVoice")[:100],
                Path(original["reference"]),
                render.ffmpeg_path(),
                job,
                "openvoice",
            )
            new_id = result["voice_id"]
            path = core.DATA / "voices" / new_id / "voice.json"
            voice = core.read_json(path)
            voice["source_voice_id"] = voice_id
            core.write_json(path, voice)
        return speech.preview_voice(new_id, body.text, job, render.ffmpeg_path())

    return jobs.submit("voice-preview", "Probar clonación con OpenVoice V2", prepare)


@app.post("/api/voices/{voice_id}/preview")
def generate_voice_preview(voice_id: str, body: VoicePreview):
    speech.get_voice(voice_id)
    return jobs.submit(
        "voice-preview",
        "Ejemplo de voz",
        lambda job: speech.preview_voice(
            voice_id, body.text, job, render.ffmpeg_path()
        ),
    )


@app.get("/api/voices/{voice_id}/{audio_kind}")
def voice_audio(voice_id: str, audio_kind: str):
    if audio_kind not in {"reference", "preview"}:
        raise ValueError("Tipo de audio inválido.")
    path = core.DATA / "voices" / core.identifier(voice_id) / f"{audio_kind}.wav"
    return file_response(path, media_type="audio/wav")


@app.delete("/api/voices/{voice_id}")
def remove_voice(voice_id: str):
    # Se retira del selector, conservando referencias para renders guardados.
    path = core.DATA / "voices" / core.identifier(voice_id) / "voice.json"
    if not path.exists():
        raise FileNotFoundError("Voz inexistente.")
    path.rename(path.with_name("voice.archived.json"))
    return {"ok": True}


@app.get("/api/audio/{key}")
def cached_audio(key: str):
    import re

    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Audio inválido.")
    return file_response(
        core.DATA / "cache/audio" / f"{key}.wav", media_type="audio/wav"
    )


@app.post("/api/engine/install")
def install_voice_engine():
    for j in job_list():
        if j["kind"] == "install" and j["status"] in {"running", "queued"}:
            return j
    return jobs.submit("install", "Instalar clonador local", speech.install_engine)


@app.post("/api/engine/optimize")
def optimize_voice_cpu():
    return jobs.submit(
        "cpu-optimize", "Optimizar voz para esta CPU", speech.optimize_cpu
    )


@app.post("/api/engine/openvoice/install")
def install_openvoice_engine():
    return jobs.submit(
        "openvoice-install",
        "Instalar OpenVoice V2 para español",
        speech.install_openvoice,
    )


@app.get("/api/exports")
def export_list():
    core.ensure_data()
    return sorted(
        [core.read_json(p) for p in (core.DATA / "exports").glob("*/manifest.json")],
        key=lambda m: m["created"],
        reverse=True,
    )


@app.get("/api/exports/{export_id}/{kind}")
def exported_file(export_id: str, kind: str, download: bool = False):
    directory = core.DATA / "exports" / core.identifier(export_id)
    files = {
        "video": ("video.mp4", "video/mp4"),
        "timeline": ("timeline.json", "application/json"),
        "subtitles": ("subtitles.srt", "text/plain; charset=utf-8"),
        "snapshot": ("project-snapshot.json", "application/json"),
    }
    if kind not in files:
        raise ValueError("Archivo de exportación inválido.")
    name, media_type = files[kind]
    kwargs = {"media_type": media_type}
    if download:
        manifest = core.read_json(directory / "manifest.json", {})
        import re

        title = re.sub(r"[^\w -]", "", manifest.get("project_name", "video"))[:70]
        kwargs["filename"] = (
            f"{title}-{manifest.get('aspect', '').replace(':', 'x')}-{manifest.get('resolution', '')}{Path(name).suffix}"
        )
    return file_response(directory / name, **kwargs)


@app.post("/api/exports/{export_id}/open-folder")
def open_export_folder(export_id: str):
    directory = core.DATA / "exports" / core.identifier(export_id)
    if not (directory / "manifest.json").is_file():
        raise FileNotFoundError("Exportación inexistente.")
    if os.name == "nt":
        os.startfile(directory)
    return {"path": str(directory)}


app.mount("/static", StaticFiles(directory=core.ROOT / "web"), name="static")
