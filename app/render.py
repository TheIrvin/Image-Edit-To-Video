from __future__ import annotations

import math
import shutil
import time
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .core import DATA, dimensions, scene_plan, write_json
from .speech import get_voice, synthesize, wav_duration


def ffmpeg_path():
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def crop_box(width, height, ratio, x, y):
    if width / height > ratio:
        cw, ch = height * ratio, height
    else:
        cw, ch = width, width / ratio
    left = min(max(width * x - cw / 2, 0), width - cw)
    top = min(max(height * y - ch / 2, 0), height - ch)
    return (left, top, left + cw, top + ch)


def prepare_image(
    source: Path, destination: Path, width, height, scene, settings, *, overscan=True
):
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    # Margen para movimientos lentos, sin bordes negros.
    factor = 1.15 if overscan else 1
    w, h = round(width * factor), round(height * factor)
    fit = settings["fit"] if scene["fit"] == "inherit" else scene["fit"]
    if fit == "blur":
        background = image.crop(
            crop_box(*image.size, w / h, scene["focus_x"], scene["focus_y"])
        )
        canvas = background.resize((w, h), Image.Resampling.LANCZOS).filter(
            ImageFilter.GaussianBlur(max(12, w / 45))
        )
        canvas = ImageEnhance.Brightness(canvas).enhance(0.55)
        # El sujeto completo conserva un margen incluso durante el zoom.
        foreground = ImageOps.contain(
            image, (round(w * 0.86), round(h * 0.86)), Image.Resampling.LANCZOS
        )
        canvas.paste(
            foreground, ((w - foreground.width) // 2, (h - foreground.height) // 2)
        )
    else:
        canvas = image.crop(
            crop_box(*image.size, w / h, scene["focus_x"], scene["focus_y"])
        ).resize((w, h), Image.Resampling.LANCZOS)
    effect = scene["filter"]
    if effect == "mono":
        canvas = ImageOps.grayscale(canvas).convert("RGB")
    elif effect == "sepia":
        canvas = ImageOps.colorize(ImageOps.grayscale(canvas), "#231a11", "#f4d9a2")
    elif effect in {"warm", "cool"}:
        red, green, blue = canvas.split()
        red = red.point(lambda n: min(255, n * (1.07 if effect == "warm" else 0.96)))
        blue = blue.point(lambda n: min(255, n * (0.94 if effect == "warm" else 1.08)))
        canvas = Image.merge("RGB", (red, green, blue))
    elif effect == "cinema":
        canvas = ImageEnhance.Color(canvas).enhance(0.78)
        canvas = ImageEnhance.Contrast(canvas).enhance(1.12)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)


def motion_filter(motion, frames, width, height, fps):
    p = f"on/{max(1, frames - 1)}"
    z, x, y = "1.08", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    if motion == "zoom_in":
        z = f"1+0.08*{p}"
    elif motion == "zoom_out":
        z = f"1.08-0.08*{p}"
    elif motion == "pan_right":
        x = f"(iw-iw/zoom)*{p}"
    elif motion == "pan_left":
        x = f"(iw-iw/zoom)*(1-{p})"
    elif motion == "pan_down":
        y = f"(ih-ih/zoom)*{p}"
    elif motion == "pan_up":
        y = f"(ih-ih/zoom)*(1-{p})"
    elif motion == "diagonal_in":
        z, x, y = f"1+0.08*{p}", f"(iw-iw/zoom)*{p}", f"(ih-ih/zoom)*{p}"
    elif motion == "diagonal_out":
        z, x, y = f"1.08-0.08*{p}", f"(iw-iw/zoom)*(1-{p})", f"(ih-ih/zoom)*(1-{p})"
    elif motion == "still":
        z = "1"
    # Un fotograma de reserva permite a fps confirmar el último intervalo; el
    # encoder limita la salida al número exacto de frames de la escena.
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames + 1}:s={width}x{height}:fps={fps},format=yuv420p,setpts=PTS-STARTPTS,fps={fps}"


def segment_duration(audio_seconds, pause, extra_pause, fps):
    frames = max(1, math.ceil((audio_seconds + pause + extra_pause) * fps - 1e-8))
    return frames, frames / fps


def concatenate_audio(paths, durations, destination):
    with wave.open(str(destination), "wb") as combined:
        combined.setnchannels(1)
        combined.setsampwidth(2)
        combined.setframerate(24000)
        for path, duration in zip(paths, durations):
            with wave.open(str(path), "rb") as source:
                if (
                    source.getnchannels(),
                    source.getsampwidth(),
                    source.getframerate(),
                ) != (1, 2, 24000):
                    raise ValueError("Formato de audio interno incorrecto.")
                frames = source.getnframes()
                combined.writeframes(source.readframes(frames))
            # Cada escena tiene su pausa completa; no se mezclan voces de escenas vecinas.
            silence = round(duration * 24000) - frames
            if silence < 0:
                raise ValueError("Una escena es más corta que su audio.")
            combined.writeframes(b"\0\0" * silence)


def srt_time(seconds):
    ms = round(seconds * 1000)
    return (
        f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    )


def write_subtitles(scenes, timing, destination):
    import re

    # Subtítulos por escena, sin prometer alineación palabra por palabra.
    lines = []
    for i, (scene, entry) in enumerate(zip(scenes, timing)):
        caption = re.sub(r"\n[ \t]*\n+", "\n", scene["text"])
        lines.extend(
            [
                str(i + 1),
                f"{srt_time(entry['start'])} --> {srt_time(entry['start'] + entry['audio_duration'])}",
                caption,
                "",
            ]
        )
    destination.write_text("\n".join(lines), encoding="utf-8")


def render(project, job, *, preview=False, only_scene=None, reuse_audio_only=False):
    ffmpeg = ffmpeg_path()
    settings = dict(project["settings"])
    if preview:
        settings["resolution"] = "720"
    width, height = dimensions(settings)
    indexed = list(enumerate(project["scenes"]))
    if only_scene is not None:
        selected = next(
            (n for n, (_, s) in enumerate(indexed) if s["number"] == only_scene), None
        )
        if selected is None:
            raise ValueError("La escena no existe.")
        # Se incluye la anterior para poder revisar también la transición de entrada.
        indexed = indexed[max(0, selected - 1) : selected + 1]
    scenes = [s for _, s in indexed]
    voice = get_voice(settings["voice_id"])
    audio = synthesize(scenes, voice, settings["speed"], job, ffmpeg)
    if reuse_audio_only:
        return {
            "project_revision": project["revision"],
            "scenes": [
                {"number": s["number"], "audio_duration": wav_duration(p)}
                for s, p in zip(scenes, audio)
            ],
        }
    directory = DATA / "exports" / job.id
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "project-snapshot.json", project)
    work = directory / "work"
    work.mkdir(exist_ok=True)
    clips, timing, durations = [], [], []
    position = 0
    previous = None
    fps = settings["fps"]
    for n, ((index, scene), audio_path) in enumerate(zip(indexed, audio)):
        job.check()
        audio_duration = wav_duration(audio_path)
        frames, duration = segment_duration(
            audio_duration, settings["pause"], scene["extra_pause"], fps
        )
        motion, transition = scene_plan(scene, index, settings["seed"])
        if previous is None:
            transition = "cut"
        incoming = min(settings["transition_duration"], duration * 0.25)
        base = work / f"base-{n}.png"
        image_path = DATA / "projects" / project["id"] / "images" / scene["image"]
        prepare_image(image_path, base, width, height, scene, settings)
        clip = work / f"clip-{n:05}.mp4"
        job.update(
            progress=45 + 48 * n / len(scenes),
            message=f"Editando img{scene['number']} · {n + 1}/{len(scenes)} · {duration:.2f} s",
        )
        command = [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-filter_complex_threads",
            "1",
            "-i",
            str(base),
        ]
        current_filter = motion_filter(motion, frames, width, height, fps)
        if transition != "cut":
            command += [
                "-loop",
                "1",
                "-framerate",
                str(fps),
                "-t",
                f"{incoming:.6f}",
                "-i",
                str(previous),
            ]
            filters = f"[0:v]{current_filter}[current];[1:v]format=yuv420p,setpts=PTS-STARTPTS,fps={fps}[prev];[prev][current]xfade=transition={transition}:duration={incoming:.6f}:offset=0[out]"
        else:
            filters = f"[0:v]{current_filter}[out]"
        command += [
            "-filter_complex",
            filters,
            "-map",
            "[out]",
            "-an",
            "-frames:v",
            str(frames),
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20" if not preview else "24",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "4",
            "-movflags",
            "+faststart",
            str(clip),
        ]
        job.run(command)
        previous = work / f"last-{n}.png"
        job.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-i",
                str(clip),
                "-vf",
                f"select=eq(n\\,{frames - 1})",
                "-frames:v",
                "1",
                str(previous),
            ]
        )
        if not previous.exists():
            raise RuntimeError(
                "No se pudo obtener el fotograma final para la transición."
            )
        clips.append(clip)
        durations.append(duration)
        timing.append(
            {
                "number": scene["number"],
                "start": position,
                "audio_duration": audio_duration,
                "duration": duration,
                "frames": frames,
                "motion": motion,
                "transition": transition,
            }
        )
        position += duration
        base.unlink(missing_ok=True)
    job.update(progress=94, message="Uniendo imágenes y narración…")
    combined = work / "narration.wav"
    concatenate_audio(audio, durations, combined)
    concat_file = work / "clips.txt"
    # Solo nombres internos, sin rutas ni nombres aportados por el usuario.
    concat_file.write_text(
        "\n".join(f"file '{clip.name}'" for clip in clips), encoding="utf-8"
    )
    output = directory / "video.mp4"
    job.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(concat_file),
            "-i",
            str(combined),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{position:.6f}",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    write_json(directory / "timeline.json", timing)
    subtitles = directory / "subtitles.srt"
    write_subtitles(scenes, timing, subtitles)
    manifest = {
        "id": job.id,
        "project_id": project["id"],
        "project_revision": project["revision"],
        "project_name": project["name"],
        "created": time.time(),
        "preview": preview or only_scene is not None,
        "aspect": settings["aspect"],
        "resolution": settings["resolution"],
        "width": width,
        "height": height,
        "duration": position,
        "voice_name": voice["name"],
        "voice_id": voice["id"],
        "scenes": len(scenes),
        "timing": timing,
        "video_url": f"/api/exports/{job.id}/video",
        "timeline_url": f"/api/exports/{job.id}/timeline",
        "subtitles_url": f"/api/exports/{job.id}/subtitles",
        "subtitles_enabled": settings["subtitles"],
    }
    write_json(directory / "manifest.json", manifest)
    # Todos los archivos borrados pertenecen al render actual. MP4 y fuentes quedan guardados.
    shutil.rmtree(work)
    return manifest
