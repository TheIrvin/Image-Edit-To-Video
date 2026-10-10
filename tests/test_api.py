from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app import core


def test_rename_voice_preserves_profile_and_project_selection(isolated_data):
    voice_id = "a" * 32
    path = isolated_data / "voices" / voice_id / "voice.json"
    voice = {"id": voice_id, "name": "Anterior", "engine": "openvoice", "created": 1,
             "reference": "reference.wav", "speaker_embedding": [0.1, 0.2]}
    core.write_json(path, voice)
    project_path = isolated_data / "projects" / ("b" * 32) / "project.json"
    project = {"settings": {"voice_id": voice_id}}
    core.write_json(project_path, project)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.patch(f"/api/voices/{voice_id}", json={"name": "  Mi narrador  "})
        assert response.status_code == 200
        assert core.read_json(path) == dict(voice, name="Mi narrador")
        assert core.read_json(project_path) == project
        assert client.patch(f"/api/voices/{voice_id}", json={"name": "   "}).status_code == 400
        assert client.patch(f"/api/voices/{voice_id}", json={"name": "x" * 101}).status_code == 422
        assert client.patch(f"/api/voices/{'c' * 32}", json={"name": "Otro"}).status_code == 404
        assert client.patch("/api/voices/not-an-id", json={"name": "Otro"}).status_code == 400
    assert core.read_json(path)["name"] == "Mi narrador"


def test_import_save_conflict_and_file_access(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGB", (160, 90), "green").save(images / "stick8.png")
    script = tmp_path / "narration.txt"
    script.write_text("Una historia sin etiquetas.", encoding="utf-8")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/projects", json={"folder": str(images), "script": str(script)}
        )
        assert response.status_code == 200
        p = response.json()
        assert p["scenes"][0]["source_name"] == "stick8.png"
        assert (
            client.get(
                f"/api/projects/{p['id']}/scenes/1/image?aspect=9:16"
            ).status_code
            == 200
        )
        body = {k: p[k] for k in ("name", "revision", "settings", "scenes")}
        assert client.put(f"/api/projects/{p['id']}", json=body).status_code == 200
        assert client.put(f"/api/projects/{p['id']}", json=body).status_code == 409
        assert client.get("/api/projects/not-an-id").status_code == 400
        assert (
            client.post(
                "/api/projects",
                json={"folder": str(images), "script": str(script)},
                headers={"Origin": "https://unrelated.example"},
            ).status_code
            == 403
        )
