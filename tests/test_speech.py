from scripts.voice_worker import chunks

import json
import shutil
import uuid
import wave
from pathlib import Path

import pytest

from app import speech
from app.jobs import Cancelled


def test_chunking_preserves_all_words_and_punctuation():
    text = (
        "La primera vez que Nico murió, tenía la mano de un desconocido entre los dedos.\n\n"
        + "Mientras el agua ocupaba el pasillo, alguien había conseguido abrir una salida. "
        * 12
    )
    phrases = list(chunks(text))
    assert len(phrases) > 1
    assert " ".join(phrases).split() == text.split()
    assert max(map(len, phrases)) <= 220


class FixtureWorker:
    """Worker controlado para comprobar caché y cancelación, sin descargar TTS."""

    def __init__(self, stop_after_first=False):
        self.id = uuid.uuid4().hex
        self.stop_after_first = stop_after_first
        self.batches = []

    def update(self, **values):
        pass

    def run(self, command, progress=None):
        if command[0] == "fixture-ffmpeg":
            source = command[command.index("-i") + 1]
            shutil.copyfile(source, command[-1])
            return
        request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        self.batches.append(request["items"])
        for index, item in enumerate(request["items"]):
            with wave.open(item["output"], "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(b"\0\0" * 24000)
            Path(request["progress"]).write_text(
                json.dumps({"done": index + 1, "message": "Escena terminada"}),
                encoding="utf-8",
            )
            progress()
            if self.stop_after_first:
                raise Cancelled()


VOICE = {"id": "test", "engine": "sapi", "language": "es", "system_name": "Fixture"}


def test_repeated_text_is_generated_once():
    job = FixtureWorker()
    outputs = speech.synthesize(
        [{"text": "Hola."}, {"text": "Hola."}], VOICE, 1, job, "fixture-ffmpeg"
    )
    assert outputs[0] == outputs[1]
    assert outputs[0].is_file()
    assert len(job.batches[0]) == 1


def test_cancel_preserves_completed_audio_for_retry():
    items = [{"text": "Primero."}, {"text": "Segundo."}]
    first = FixtureWorker(stop_after_first=True)
    with pytest.raises(Cancelled):
        speech.synthesize(items, VOICE, 1, first, "fixture-ffmpeg")
    retry = FixtureWorker()
    outputs = speech.synthesize(items, VOICE, 1.0, retry, "fixture-ffmpeg")
    assert all(p.is_file() for p in outputs)
    assert [i["text"] for i in retry.batches[0]] == ["Segundo."]
