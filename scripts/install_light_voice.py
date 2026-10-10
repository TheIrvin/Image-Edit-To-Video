import sys, uuid, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.jobs import Job
from app.core import ensure_data, write_json
from app.speech import install_light_engine

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ensure_data()
    engine = sys.argv[1]
    job = Job(uuid.uuid4().hex)
    write_json(job.path, {'id': job.id, 'status': 'running', 'kind': engine+'-install', 'created': time.time(), 'label': 'Instalar '+engine})
    update = job.update
    def report(**values):
        print(values.get('message', ''), flush=True)
        update(**values)
    job.update = report
    try:
        result = install_light_engine(engine, job)
        job.update(status='done', progress=100, message=engine+' listo', result=result)
    except Exception as exc:
        job.update(status='error', message=str(exc))
        raise
