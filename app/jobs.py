from __future__ import annotations

import concurrent.futures
import os
import subprocess
import threading
import time
import uuid

from .core import DATA, ensure_data, identifier, read_json, write_json

EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="pipeline"
)
EVENTS: dict[str, threading.Event] = {}


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, job_id: str):
        self.id = identifier(job_id)
        self.path = DATA / "jobs" / f"{self.id}.json"
        self.cancel = EVENTS.setdefault(self.id, threading.Event())

    def update(self, **values):
        from .core import LOCK

        with LOCK:
            data = read_json(self.path)
            data.update(values, updated=time.time())
            write_json(self.path, data)

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("Operación cancelada.")

    def run(self, command: list[str], *, progress=None, cwd=None):
        self.check()
        log = DATA / "jobs" / f"{self.id}.log"
        with log.open("ab") as output:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=output,
                stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                while process.poll() is None:
                    self.check()
                    if progress:
                        progress()
                    time.sleep(0.2)
                if process.returncode != 0:
                    tail = log.read_bytes()[-6000:].decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"Falló el proceso ({process.returncode}).\n{tail}"
                    )
            except BaseException:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    else:
                        process.kill()
                    process.wait()
                raise


def submit(kind: str, label: str, fn, *, project_id=None):
    ensure_data()
    job_id = uuid.uuid4().hex
    job = Job(job_id)
    write_json(
        job.path,
        {
            "id": job_id,
            "kind": kind,
            "label": label,
            "project_id": project_id,
            "status": "queued",
            "progress": 0,
            "message": "En cola",
            "created": time.time(),
            "updated": time.time(),
        },
    )

    def work():
        try:
            job.check()
            job.update(status="running", message="Preparando…")
            result = fn(job)
            job.check()
            job.update(status="done", progress=100, message="Terminado", result=result)
        except Cancelled:
            job.update(
                status="cancelled",
                message="Cancelado. El audio ya generado queda en caché.",
            )
        except Exception as exc:
            job.update(status="error", message=str(exc), error=str(exc))
        finally:
            EVENTS.pop(job_id, None)

    EXECUTOR.submit(work)
    return read_json(job.path)


def recover_jobs():
    ensure_data()
    for file in (DATA / "jobs").glob("*.json"):
        job = read_json(file)
        if job["status"] in {"running", "queued"}:
            job.update(
                status="error",
                message="La app se cerró durante esta tarea. Puedes generarla otra vez; se reutilizará el audio en caché.",
            )
            write_json(file, job)
