"""Environment-local approval records shared by console, gateway and workers."""

import json
import re
import sys
from pathlib import Path

EMPTY_REVISION = '0' * 32
_NAME = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?\Z')


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def environment_directory():
    return Path(sys.prefix).resolve() / '.reqlica-packages'


def read_state():
    path = environment_directory() / 'state.json'
    if not path.exists():
        return {'revision': EMPTY_REVISION, 'approved': {}}
    if path.is_symlink() or path.stat().st_size > 262144:
        raise ValueError('Invalid runtime package state')
    data = json.loads(path.read_bytes())
    if not re.fullmatch(r'[0-9a-f]{32}', data.get('revision', '')) or not isinstance(data.get('approved'), dict):
        raise ValueError('Invalid runtime package state')
    for name, version in data['approved'].items():
        if not _NAME.fullmatch(name) or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.!+_-]{0,79}', version):
            raise ValueError('Invalid approved runtime package')
    return data


def dependency_revision():
    state = read_state()
    if state.get('installing'):
        raise ValueError('Runtime package installation is in progress; wait, then rerun validation and tests. If interrupted, reload Settings > Python packages to recover its saved status.')
    return state['revision']
