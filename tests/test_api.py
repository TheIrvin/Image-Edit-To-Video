from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


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
