"""Read-only production configuration check; does not import or start app.py."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

try:
    from .ops_common import ROOT, validate_database
except ImportError:
    from ops_common import ROOT, validate_database

sys.path.insert(0, str(ROOT))
from config import PIN_MAX_LENGTH, PIN_MIN_LENGTH, WEAK_PINS


def _enabled(value):
    return str(value).lower() in {'1', 'true', 'yes', 'on'}


def _placeholder(value):
    return any(word in value.lower() for word in ('change_me', 'replace_me', 'pending', 'todo', 'example'))


def check(environ=None, *, inspect_database=True):
    env = dict(os.environ if environ is None else environ)
    errors, warnings = [], []
    secret = env.get('AMORINO_SECRET_KEY', '')
    if len(secret) < 64 or len(set(secret)) < 8 or _placeholder(secret) or secret == 'amorino-gelato-2026':
        errors.append('AMORINO_SECRET_KEY must be an independently generated secret of at least 64 characters.')
    pin = env.get('AMORINO_SUPERVISOR_PIN', '').strip()
    sequential = len(pin) >= 4 and pin.isdigit() and {ord(b) - ord(a) for a, b in zip(pin, pin[1:])} in ({1}, {-1})
    if (not PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH or len(set(pin)) < 2
            or pin.lower() in WEAK_PINS or sequential or _placeholder(pin)):
        errors.append('AMORINO_SUPERVISOR_PIN must meet the application PIN policy and contain no placeholder.')
    if not _enabled(env.get('AMORINO_HTTPS', '')):
        errors.append('AMORINO_HTTPS must be enabled after a working HTTPS proxy is configured.')
    if not _enabled(env.get('AMORINO_TRUST_PROXY', '')):
        errors.append('The supplied production layout requires AMORINO_TRUST_PROXY=1 behind its single controlled proxy.')
    for name in ('AMORINO_DEBUG', 'FLASK_DEBUG', 'AMORINO_DEMO'):
        if _enabled(env.get(name, '')):
            errors.append(f'{name} must be disabled in production.')
    if env.get('AMORINO_SEED') != '0':
        errors.append('AMORINO_SEED=0 is required; never initialise production with the source roster.')
    if env.get('FLASK_ENV', '').lower() == 'development':
        errors.append('FLASK_ENV must not select development.')
    if env.get('AMORINO_HOST', '127.0.0.1') not in ('127.0.0.1', '::1', 'localhost'):
        errors.append('AMORINO_HOST must bind to loopback; only the proxy is public.')
    if env.get('AMORINO_PROCESSES') != '1':
        errors.append('Declare AMORINO_PROCESSES=1 and use the supplied single-process wsgi.py service.')
    for name in ('WEB_CONCURRENCY', 'AMORINO_WORKERS'):
        if env.get(name) not in (None, '', '1'):
            errors.append(f'{name} requests multiple processes; the login limiter requires one.')
    worker_flags = re.findall(r'(?:--workers(?:=|\s+)|-w\s*)(\d+)', env.get('GUNICORN_CMD_ARGS', ''))
    if any(value != '1' for value in worker_flags):
        errors.append('GUNICORN_CMD_ARGS must not request more than one worker.')
    for name, low, high, default in (('AMORINO_PORT', 1, 65535, '8000'), ('AMORINO_THREADS', 1, 64, '8')):
        try:
            if not low <= int(env.get(name, default)) <= high:
                raise ValueError
        except ValueError:
            errors.append(f'{name} must be an integer between {low} and {high}.')
    if env.get('TZ') != 'Europe/Lisbon':
        errors.append('Set TZ=Europe/Lisbon; on Windows also verify the operating-system timezone.')
    if env.get('AMORINO_STORAGE') != 'local':
        errors.append('Declare AMORINO_STORAGE=local after verifying persistent local storage, not SMB/NFS.')
    value = env.get('AMORINO_DB_PATH', '')
    database = Path(value) if value else None
    if database is None or not database.is_absolute():
        errors.append('AMORINO_DB_PATH must explicitly name an absolute database path.')
    elif value.startswith(('\\\\', '//')):
        errors.append('A network-share database is unsupported.')
    elif database.resolve().is_relative_to(ROOT):
        errors.append('Production data must be outside the source checkout; repository database was not opened.')
    elif inspect_database:
        try:
            if database.with_name(database.name + '.restore-in-progress').exists():
                raise ValueError('An incomplete restore marker exists')
            if database.with_name(database.name + '.migration-in-progress').exists():
                raise ValueError('An incomplete migration marker exists')
            validate_database(database)
            if not os.access(database, os.W_OK) or not os.access(database.parent, os.W_OK):
                errors.append('The service account needs database and directory write access for WAL/SHM.')
        except Exception as exc:
            # Error class only: no credentials, SQL or personal records in output.
            errors.append(f'Database validation failed ({type(exc).__name__}); inspect path, permissions and schema locally.')
    warnings.append('Configuration declarations do not verify public HTTPS, mounted storage, running process count, monitoring or off-server copies. Complete the production acceptance checks.')
    return {'ok': not errors, 'errors': errors, 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='machine-readable result without secrets')
    args = parser.parse_args()
    result = check()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print('PASS' if result['ok'] else 'FAIL')
        for message in result['errors'] + result['warnings']:
            print(message)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
