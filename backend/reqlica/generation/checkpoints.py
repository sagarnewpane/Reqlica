"""Private agent checkpoints and source-path boundaries; no generation pipeline."""
import fcntl
import json
import os
import stat
from contextlib import contextmanager
from pathlib import PurePosixPath
from time import time
from reqlica.generation.errors import GenerationError
from reqlica.projects.schema_plan import SCHEMA_PLAN_PATH
from reqlica.projects.versions import atomic_write_json

MAX_JOB_BYTES = 4 * 1024 * 1024

@contextmanager
def _lease(path, label="generation"):
    # Separate from the project lock: provider calls must not block other project work.
    descriptor = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Generation lock must be a regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GenerationError(f'This {label} already has a request in progress.', 409, code='generation_busy') from None
        yield
    finally:
        os.close(descriptor)

def _read(path):
    with open(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JOB_BYTES:
            raise ValueError('Generation checkpoint exceeds its size limit')
        return json.load(stream)

def _save(path, job):
    job['updated_at'] = time()
    if len(json.dumps(job).encode()) > MAX_JOB_BYTES:
        raise ValueError('Generation checkpoint exceeds its size limit')
    atomic_write_json(path, job)

def _file_path(value):
    if value in {SCHEMA_PLAN_PATH, "database/migration.json"}:
        return
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or '\\' in value
        or len(path.parts) < 2 or path.parts[0] not in {'endpoints', 'models', 'tests'}
        or path.suffix != '.py' or not path.stem.isidentifier()
        or any(not part.isidentifier() or part in {'__pycache__', 'versions', 'drafts'} for part in path.parts[:-1])):
        raise ValueError('Use canonical Python paths under endpoints/, models/, tests/, database/schema.json or database/migration.json')
