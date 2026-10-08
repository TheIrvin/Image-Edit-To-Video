from scripts.voice_worker import chunks


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
