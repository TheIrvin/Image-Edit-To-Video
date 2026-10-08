import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.jobs import Job
from app.core import ensure_data, write_json
from app.speech import install_engine
import uuid

if __name__ == "__main__":
    ensure_data()
    job = Job(uuid.uuid4().hex)
    write_json(
        job.path, {"id": job.id, "status": "running", "kind": "install", "created": 0}
    )
    original_update = job.update

    def update(**values):
        print(values.get("message", ""), flush=True)
        original_update(**values)

    job.update = update
    try:
        install_engine(job)
        job.update(status="done", message="Chatterbox Multilingual listo.")
    except Exception as exc:
        job.update(status="error", message=str(exc))
        raise
