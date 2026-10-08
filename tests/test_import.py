import pytest
from PIL import Image

from app.core import audio_key, create_project, parse_script


def images(tmp_path, names):
    directory = tmp_path / "images"
    directory.mkdir()
    for name in names:
        Image.new("RGB", (160, 90), "#597d85").save(directory / name)
    return directory


def test_labels_are_not_narrated():
    scenes = parse_script("\ufeffimg1:\nUna historia.\n\nimg2:\nOtro momento.")
    assert scenes == [
        {"number": 1, "text": "Una historia."},
        {"number": 2, "text": "Otro momento."},
    ]


def test_inline_labels_are_also_separators():
    assert parse_script("img1: Una historia.\n\nimg2: Otro momento.") == [
        {"number": 1, "text": "Una historia."},
        {"number": 2, "text": "Otro momento."},
    ]


def test_paragraphs_accept_multiline_text_and_crlf():
    scenes = parse_script(
        "Primer párrafo,\r\nque continúa.\r\n\r\n\r\nSegundo párrafo."
    )
    assert len(scenes) == 2
    assert scenes[0]["text"] == "Primer párrafo,\nque continúa."
    assert scenes[0]["number"] is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "img1:\nHola.\nimg1:\nDuplicado.",
        "img1:\n\nimg2:\nHola.",
        "Antes.\nimg1:\nHola.",
    ],
)
def test_invalid_scripts_fail_before_render(text):
    with pytest.raises(ValueError):
        parse_script(text)


def test_stick8_is_first_scene_even_with_labels(tmp_path):
    directory = images(tmp_path, ["stick10.png", "stick8.png", "stick9.png"])
    script = tmp_path / "script.txt"
    script.write_text("img1:\nUno.\n\nimg2:\nDos.\n\nimg3:\nTres.", encoding="utf-8")
    p = create_project(directory, script)
    assert [s["source_name"] for s in p["scenes"]] == [
        "stick8.png",
        "stick9.png",
        "stick10.png",
    ]
    assert [s["number"] for s in p["scenes"]] == [1, 2, 3]


def test_unlabelled_paragraphs_use_natural_order(tmp_path):
    directory = images(tmp_path, ["stick10.png", "stick8.png", "stick9.png"])
    script = tmp_path / "script.txt"
    script.write_text("Uno.\n\nDos.\n\nTres.", encoding="utf-8")
    p = create_project(directory, script)
    assert p["script_mode"] == "paragraphs"
    assert [s["source_name"] for s in p["scenes"]] == [
        "stick8.png",
        "stick9.png",
        "stick10.png",
    ]


def test_number_pairing_is_optional(tmp_path):
    directory = images(tmp_path, ["stick8.png", "stick50.png"])
    script = tmp_path / "script.txt"
    script.write_text(
        "img50:\nPrimero en el TXT.\n\nimg8:\nSegundo en el TXT.", encoding="utf-8"
    )
    p = create_project(directory, script, pairing="number")
    assert [s["source_name"] for s in p["scenes"]] == ["stick50.png", "stick8.png"]


def test_missing_paragraph_image_is_an_error(tmp_path):
    directory = images(tmp_path, ["stick8.png"])
    script = tmp_path / "script.txt"
    script.write_text("Uno.\n\nDos.", encoding="utf-8")
    with pytest.raises(ValueError, match="2 párrafos"):
        create_project(directory, script)


def test_audio_cache_survives_preview_metadata_changes():
    voice = {
        "id": "demo",
        "engine": "chatterbox",
        "language": "es",
        "reference": "reference.wav",
        "created": 1,
        "preview": None,
    }
    before = audio_key("Hola", voice, 1)
    voice.update(preview="preview.wav", preview_text="Otra frase")
    assert audio_key("Hola", voice, 1) == before
    assert audio_key("Adiós", voice, 1) != before
