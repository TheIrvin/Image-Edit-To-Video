from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("IMAGE_VIDEO_DATA", str(ROOT / "data"))).resolve()
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MOTIONS = {
    "auto",
    "zoom_in",
    "zoom_out",
    "pan_right",
    "pan_left",
    "pan_down",
    "pan_up",
    "diagonal_in",
    "diagonal_out",
    "still",
    "push_focus",
    "reveal",
    "drift",
}
TRANSITIONS = {
    "auto",
    "cut",
    "fade",
    "fadeblack",
    "wipeleft",
    "wiperight",
    "slideleft",
    "slideright",
    "circleopen",
}
FILTERS = {"none", "warm", "cool", "mono", "sepia", "cinema"}
RESOLUTIONS = {
    "720": (1280, 720),
    "1080": (1920, 1080),
    "1260": (2240, 1260),
    "2k": (2560, 1440),
}
LOCK = threading.RLock()


def ensure_data():
    for directory in ("projects", "voices", "jobs", "exports", "cache"):
        (DATA / directory).mkdir(parents=True, exist_ok=True)


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Identificador inválido.")
    return value


def read_json(path: Path, default=None):
    with LOCK:
        return (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        )


def write_json(path: Path, value):
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(path)


def parse_script(text: str) -> list[dict]:
    """Los identificadores emparejan imágenes; nunca se envían al sintetizador."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    headers = list(
        re.finditer(r"(?im)^[ \t]*img[ \t]*(\d+)[ \t]*:[ \t]*(?:\\)?[ \t]*", text)
    )
    if not headers:
        paragraphs = [
            p.strip() for p in re.split(r"\n[ \t]*\n(?:[ \t]*\n)*", text) if p.strip()
        ]
        if not paragraphs:
            raise ValueError("El TXT de narración está vacío.")
        return [{"number": None, "text": p} for p in paragraphs]
    if text[: headers[0].start()].strip():
        raise ValueError("Hay texto antes del primer separador imgN:.")
    scenes, seen = [], set()
    for i, header in enumerate(headers):
        number = int(header.group(1))
        if number in seen:
            raise ValueError(f"El separador img{number}: está repetido.")
        if number < 1:
            raise ValueError("La numeración debe empezar en 1 o un número mayor.")
        seen.add(number)
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        narration = text[header.end() : end].strip()
        if not narration:
            raise ValueError(f"img{number}: no contiene narración.")
        scenes.append({"number": number, "text": narration})
    return scenes


def image_number(name: str) -> int | None:
    stem = Path(name).stem
    match = re.search(r"(\d+)$", stem)
    return int(match.group(1)) if match else None


def read_script(path: Path) -> str:
    if path.suffix.lower() != ".txt" or not path.is_file():
        raise ValueError("Selecciona un archivo de narración .txt.")
    if path.stat().st_size > 5_000_000:
        raise ValueError("El TXT supera el límite de 5 MB.")
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1252")


def settings_default():
    return {
        "voice_id": "",
        "aspect": "16:9",
        "resolution": "1080",
        "fit": "crop",
        "pause": 0.1,
        "transition_duration": 0.3,
        "fps": 30,
        "speed": 1.0,
        "seed": 17,
        "subtitles": False,
        "export_root": "",
    }


def create_project(
    folder: Path, script: Path, name: str = "", pairing: str = "order"
) -> dict:
    from PIL import Image

    if not folder.is_dir():
        raise ValueError("Selecciona una carpeta de imágenes válida.")
    parsed = parse_script(read_script(script))
    if pairing not in {"order", "number"}:
        raise ValueError("Modo de emparejamiento inválido.")
    images = {}
    all_images = []
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            all_images.append(path)
            number = image_number(path.name)
            if number is not None:
                if number in images and pairing == "number":
                    raise ValueError(
                        f"Hay dos imágenes con el número {number}: {images[number].name} y {path.name}."
                    )
                images[number] = path
    script_mode = "labels" if parsed[0]["number"] is not None else "paragraphs"

    def natural_key(path):
        return [
            (0, int(p)) if p.isdigit() else (1, p.lower())
            for p in re.split(r"(\d+)", path.name)
        ]

    ordered = sorted(all_images, key=natural_key)
    if len(images) == len(all_images):
        ordered = [images[n] for n in sorted(images)]
    if pairing == "order":
        images = {i + 1: path for i, path in enumerate(ordered)}
    if script_mode == "paragraphs":
        if len(parsed) != len(all_images):
            raise ValueError(
                f"El TXT contiene {len(parsed)} párrafos y la carpeta tiene {len(all_images)} imágenes. Sin etiquetas, las cantidades deben coincidir."
            )
        # Sin etiquetas, cada párrafo se empareja siempre por orden natural.
        images = {i + 1: path for i, path in enumerate(ordered)}
        for i, scene in enumerate(parsed):
            scene["number"] = i + 1
    missing = [s["number"] for s in parsed if s["number"] not in images]
    if missing:
        raise ValueError(
            "Faltan imágenes para: "
            + ", ".join(f"img{n}" for n in missing[:25])
            + ". Nombra los archivos img7.png, img 8.jpg o 009.webp."
        )
    ensure_data()
    project_id = uuid.uuid4().hex
    directory = DATA / "projects" / project_id
    (directory / "images").mkdir(parents=True)
    scenes = []
    try:
        for scene in parsed:
            src = images[scene["number"]]
            with Image.open(src) as image:
                image.verify()
            with Image.open(src) as image:
                width, height = image.size
            filename = f"img{scene['number']}{src.suffix.lower()}"
            shutil.copy2(src, directory / "images" / filename)
            scenes.append(
                {
                    **scene,
                    "image": filename,
                    "source_name": src.name,
                    "width": width,
                    "height": height,
                    "motion": "auto",
                    "transition": "auto",
                    "filter": "none",
                    "focus_x": 0.5,
                    "focus_y": 0.5,
                    "fit": "inherit",
                    "extra_pause": 0.0,
                }
            )
        project = {
            "id": project_id,
            "name": name.strip()[:120] or script.stem,
            "script_mode": script_mode,
            "pairing": pairing,
            "created": time.time(),
            "updated": time.time(),
            "revision": 1,
            "settings": settings_default(),
            "scenes": scenes,
            "warnings": [],
        }
        unused = sorted(set(images) - {s["number"] for s in parsed})
        if unused:
            project["warnings"].append(
                f"{len(unused)} imágenes sin bloque de narración se omitieron."
            )
        shutil.copy2(script, directory / "narracion-original.txt")
        write_json(directory / "project.json", project)
        return project
    except Exception:
        # Solo la carpeta nueva creada por esta importación.
        shutil.rmtree(directory)
        raise


def get_project(project_id: str) -> dict:
    project = read_json(DATA / "projects" / identifier(project_id) / "project.json")
    if project is None:
        raise FileNotFoundError("El proyecto no existe.")
    project["settings"].setdefault("export_root", "")
    return project


def validate_project(project: dict):
    settings = project["settings"]
    root = settings.get("export_root", "")
    if not isinstance(root, str) or (root and not Path(root).is_dir()):
        raise ValueError("Selecciona una carpeta raíz de exportación existente.")
    settings["export_root"] = str(Path(root).resolve()) if root else ""
    if (
        settings["aspect"] not in {"16:9", "9:16"}
        or settings["resolution"] not in RESOLUTIONS
    ):
        raise ValueError("Formato o resolución inválidos.")
    if settings["fit"] not in {"crop", "blur"} or settings["fps"] not in {24, 30}:
        raise ValueError("Ajuste de imagen o FPS inválidos.")
    for key, lo, hi in (
        ("pause", 0, 3),
        ("transition_duration", 0.05, 1),
        ("speed", 0.8, 1.25),
    ):
        value = float(settings[key])
        if not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"Valor inválido: {key}.")
        settings[key] = value
    settings["seed"] = int(settings["seed"]) % 1_000_000
    settings["subtitles"] = bool(settings["subtitles"])
    seen = set()
    if not project["scenes"]:
        raise ValueError("El proyecto no tiene escenas.")
    for scene in project["scenes"]:
        if scene["number"] in seen or not scene["text"].strip():
            raise ValueError("Las escenas deben tener números únicos y texto.")
        seen.add(scene["number"])
        if (
            scene["motion"] not in MOTIONS
            or scene["transition"] not in TRANSITIONS
            or scene["filter"] not in FILTERS
        ):
            raise ValueError("Movimiento, transición o filtro inválido.")
        if scene["fit"] not in {"inherit", "crop", "blur"}:
            raise ValueError("Ajuste de escena inválido.")
        for key in ("focus_x", "focus_y"):
            value = float(scene[key])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("El punto de interés debe estar dentro de la imagen.")
            scene[key] = value
        value = float(scene["extra_pause"])
        if not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError("La pausa adicional debe estar entre 0 y 10 segundos.")
        scene["extra_pause"] = value


def audio_key(text: str, voice: dict, speed: float) -> str:
    stable_voice = {
        key: voice.get(key)
        for key in ("id", "engine", "language", "reference", "system_name", "created")
    }
    fingerprint = {
        "text": text,
        "voice": stable_voice,
        "speed": float(speed),
        "version": 1,
    }
    return hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def dimensions(settings):
    w, h = RESOLUTIONS[settings["resolution"]]
    return (w, h) if settings["aspect"] == "16:9" else (h, w)


def scene_plan(scene, index: int, seed: int, duration=None):
    # Reglas editoriales transparentes; no pretenden comprender la imagen.
    text = scene.get("text", "").casefold()
    time_change = bool(re.search(
        r"\b(después|más tarde|al día siguiente|durante los días|años después|pasaron)\b", text
    ))
    emphasis = bool(re.search(
        r"\b(murió|muerte|miedo|recordaba|pensó|comprendió|descubrió|secreto|miró|mano|rostro)\b", text
    ))
    reveal = bool(re.search(
        r"\b(ciudad|paisaje|mundo|calle|puente|habitación|alrededor)\b", text
    ))
    variation = int(hashlib.sha256(
        f"{seed}:{scene.get('number', index)}:{text}".encode()
    ).hexdigest()[:8], 16)
    if duration is not None and duration < 3.5:
        automatic = "still"
    elif emphasis:
        automatic = "push_focus"
    elif reveal or time_change:
        automatic = "reveal"
    else:
        automatic = ("drift", "push_focus", "reveal", "still")[variation % 4]
    motion = (
        scene["motion"]
        if scene["motion"] != "auto"
        else automatic
    )
    transition = (
        scene["transition"]
        if scene["transition"] != "auto"
        else ("fade" if time_change else "cut")
    )
    return motion, "cut" if index == 0 else transition
