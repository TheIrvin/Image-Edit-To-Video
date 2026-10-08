import json
import subprocess
import uuid
import wave

import numpy as np
import pytest
from PIL import Image

from app import core, render
from app.jobs import Job


def test_padding_never_shortens_audio():
    frames, duration = render.segment_duration(3, 0.1, 0, 30)
    assert frames == 93
    assert duration == 3.1
    for fps in (24, 30):
        for audio in (0.011, 0.987, 3.001, 12.568):
            _, duration = render.segment_duration(audio, 0.1, 0.25, fps)
            assert audio + 0.35 <= duration + 1e-8 < audio + 0.35 + 1 / fps


def test_focus_crop_preserves_point_at_edges():
    assert render.crop_box(1600, 900, 9 / 16, 0, 0.5)[0] == 0
    assert render.crop_box(1600, 900, 9 / 16, 1, 0.5)[2] == 1600


@pytest.mark.parametrize("overscan", [False, True])
def test_vertical_blur_foreground_fills_width(tmp_path, overscan):
    source = tmp_path / "landscape.png"
    destination = tmp_path / "vertical.png"
    Image.new("RGB", (1600, 900), "white").save(source)
    scene = {"fit": "inherit", "focus_x": 0.5, "focus_y": 0.5, "filter": "none"}
    render.prepare_image(
        source, destination, 180, 320, scene, {"fit": "blur"}, overscan=overscan
    )
    with Image.open(destination) as image:
        assert image.getpixel((0, image.height // 2)) == (255, 255, 255)
        assert image.getpixel((image.width - 1, image.height // 2)) == (255, 255, 255)
        assert image.getpixel((image.width // 2, 0))[0] < 200


@pytest.mark.parametrize("aspect", ["16:9", "9:16"])
def test_real_ffmpeg_transitions_have_exact_frames_and_unmixed_audio(
    tmp_path, monkeypatch, aspect
):
    directory = tmp_path / "images"
    directory.mkdir()
    kinds = [
        "cut",
        "fade",
        "fadeblack",
        "wipeleft",
        "wiperight",
        "slideleft",
        "slideright",
        "circleopen",
    ]
    for i in range(len(kinds)):
        Image.new("RGB", (480, 270), (30 * i, 90, 190)).save(
            directory / f"stick{i + 8}.png"
        )
    script = tmp_path / "narration.txt"
    script.write_text(
        "\n\n".join(f"Frase {i}." for i in range(len(kinds))), encoding="utf-8"
    )
    p = core.create_project(directory, script)
    p["settings"].update(voice_id="test", aspect=aspect, resolution="720")
    monkeypatch.setitem(core.RESOLUTIONS, "720", (320, 180))
    for scene, kind in zip(p["scenes"], kinds):
        scene["transition"] = kind
    audio = []
    for i in range(len(kinds)):
        path = tmp_path / f"audio-{i}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            tone = (
                np.sin(2 * np.pi * (220 + i * 80) * np.arange(24000) / 24000) * 8000
            ).astype("<i2")
            wav.writeframes(tone.tobytes())
        audio.append(path)
    monkeypatch.setattr(render, "get_voice", lambda _: {"id": "test", "name": "Test"})
    monkeypatch.setattr(render, "synthesize", lambda *a, **k: audio)
    job = Job(uuid.uuid4().hex)
    core.write_json(job.path, {"id": job.id, "status": "running"})
    result = render.render(p, job)
    output = core.DATA / "exports" / job.id / "video.mp4"
    assert output.is_file()
    assert result["duration"] == pytest.approx(8 * 1.1)
    decoded = subprocess.run(
        [
            render.ffmpeg_path(),
            "-v",
            "error",
            "-i",
            str(output),
            "-map",
            "0:v:0",
            "-f",
            "framehash",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    frames = [
        line
        for line in decoded.stdout.splitlines()
        if line and not line.startswith("#")
    ]
    assert len(frames) == 8 * 33
    timing = json.loads((output.parent / "timeline.json").read_text())
    assert all(t["duration"] == pytest.approx(1.1) for t in timing)
    decoded_audio = tmp_path / "decoded.wav"
    subprocess.run(
        [
            render.ffmpeg_path(),
            "-y",
            "-v",
            "error",
            "-i",
            str(output),
            "-vn",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(decoded_audio),
        ],
        check=True,
    )
    with wave.open(str(decoded_audio), "rb") as wav:
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    assert len(pcm) / 24000 == pytest.approx(result["duration"], abs=0.05)
    for i in range(len(kinds)):
        # La pausa sigue siendo silencio, incluso en transiciones largas.
        start = round((i * 1.1 + 1.045) * 24000)
        end = round((i * 1.1 + 1.085) * 24000)
        assert np.abs(pcm[start:end]).mean() < 50
