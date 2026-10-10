from scripts.voice_worker import chunks

import json
import shutil
import uuid
import wave
from pathlib import Path

import pytest

from app import speech
from app.jobs import Cancelled


@pytest.mark.parametrize("engine,speaker", [("supertonic", "M1"), ("kokoro", "em_alex")])
def test_preset_profiles_use_valid_ids_and_keep_archived_voices_retired(monkeypatch, engine, speaker):
    from app.core import identifier
    monkeypatch.setattr(speech, "preset_engine_status", lambda engine: {"profile": {"speakers": [speaker]}})
    speech.register_preset_voices(engine)
    voices = [v for v in speech.list_voices() if v["engine"] == engine]
    assert len(voices) == 1
    voice = voices[0]
    assert identifier(voice["id"]) == voice["id"]
    assert voice["kind"] == "Voz predefinida" and "reference" not in voice
    path = speech.DATA / "voices" / voice["id"] / "voice.json"
    path.rename(path.with_name("voice.archived.json"))
    speech.register_preset_voices(engine)
    assert not path.exists()


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

    def check(self):
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


def test_adapted_supertonic_uses_saved_style_without_refitting(monkeypatch):
    monkeypatch.setattr(speech, "supertonic_clone_status", lambda: {"ready": True})
    voice = dict(id=uuid.uuid4().hex, engine="supertonic-clone", language="es", style_path="saved-style.json")
    worker = FixtureWorker()
    outputs = speech.synthesize([{"text": "Una voz adaptada."}], voice, 1, worker, "fixture-ffmpeg")
    request_paths = list((speech.DATA / "jobs" / worker.id).glob("speech-request.json"))
    request = json.loads(request_paths[0].read_text(encoding="utf-8"))
    assert request["style_path"] == "saved-style.json"
    assert "reference" not in request and "speaker" not in request
    assert outputs[0].is_file()
    speech.synthesize([{"text": "Una voz adaptada."}], voice, 1, worker, "fixture-ffmpeg")
    assert len(worker.batches) == 1


def test_repeated_text_is_generated_once():
    job = FixtureWorker()
    outputs = speech.synthesize(
        [{"text": "Hola."}, {"text": "Hola."}], VOICE, 1, job, "fixture-ffmpeg"
    )
    assert outputs[0] == outputs[1]
    assert outputs[0].is_file()
    assert len(job.batches[0]) == 1


def test_sequential_voices_do_not_reuse_previous_batch_progress():
    class LoadingWorker(FixtureWorker):
        def run(self, command, progress=None):
            if progress:
                progress()  # Poll while the next model is still loading.
            super().run(command, progress=progress)
    job = LoadingWorker()
    speech.synthesize([{"text": "Primera voz."}], VOICE, 1, job, "fixture-ffmpeg")
    second = dict(VOICE, id=uuid.uuid4().hex)
    output = speech.synthesize([{"text": "Segunda voz."}], second, 1, job, "fixture-ffmpeg")[0]
    assert output.is_file()
    assert len(job.batches) == 2


def test_speed_change_reuses_original_synthesis():
    job = FixtureWorker()
    items = [{"text": "Una narración guardada."}]
    original = speech.synthesize(items, VOICE, 1, job, "fixture-ffmpeg")[0]
    faster = speech.synthesize(items, VOICE, 1.25, job, "fixture-ffmpeg")[0]
    assert original.is_file() and faster.is_file()
    assert original != faster
    assert len(job.batches) == 1


def test_cancel_preserves_completed_audio_for_retry():
    items = [{"text": "Primero."}, {"text": "Segundo."}]
    first = FixtureWorker(stop_after_first=True)
    with pytest.raises(Cancelled):
        speech.synthesize(items, VOICE, 1, first, "fixture-ffmpeg")
    retry = FixtureWorker()
    outputs = speech.synthesize(items, VOICE, 1.0, retry, "fixture-ffmpeg")
    assert all(p.is_file() for p in outputs)
    assert [i["text"] for i in retry.batches[0]] == ["Segundo."]
