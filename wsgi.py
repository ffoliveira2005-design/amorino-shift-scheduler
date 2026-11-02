"""Production entrypoint.

`python app.py` starts Werkzeug's development server. It is single-threaded by
default, it is not written to face the network, and its own documentation says
not to deploy it — but it is the obvious way to start the app, so it is what
gets used unless something else is equally obvious. This file is that something
else.

Run it directly (uses waitress, which works on Windows and Linux alike):

    python wsgi.py

Or hand `application` to any WSGI server:

    gunicorn --workers 1 --threads 8 wsgi:application     # Linux
    waitress-serve --port 8000 wsgi:application           # Windows

ONE WORKER PROCESS, THREADS FOR CONCURRENCY. This is not tuning, it is a
correctness requirement, for two reasons:

  1. The login rate limiter and its counters live in process memory
     (helpers.auth.LOGIN_ATTEMPTS). Two worker processes mean two independent
     counters, so N workers multiply every attempt budget by N — with 4 workers
     the supervisor account's 5-attempts-per-5-minutes becomes 20.
  2. app.py runs db.create_all() and its ALTER TABLE migrations at import time.
     Several processes importing at once race on the same schema change.

Threads are fine for both: they share one address space, so one counter and one
import. SQLite in WAL mode (see app.py) handles the concurrent connections.
"""
import os

# Validate before app import (which runs additive migrations). The supplied
# production service also runs this explicitly so failures reach journald.
if os.environ.get('AMORINO_HTTPS', '').lower() in ('1', 'true', 'yes'):
    from scripts.preflight import check
    _preflight = check()
    if not _preflight['ok']:
        raise RuntimeError('Production preflight failed: ' + '; '.join(_preflight['errors']))

from app import app as application

# Re-exported so `from wsgi import app` also works — the name people try first.
app = application


def _serve() -> None:
    host = os.environ.get('AMORINO_HOST', '127.0.0.1')
    port = int(os.environ.get('AMORINO_PORT', 8000))
    threads = int(os.environ.get('AMORINO_THREADS', 8))
    try:
        from waitress import serve
    except ImportError:
        raise SystemExit(
            'waitress is not installed. Either install it:\n'
            '    pip install -r requirements.txt\n'
            'or serve wsgi:application with the WSGI server of your choice.'
        )
    application.logger.info('Serving on http://%s:%s (%d threads)', host, port, threads)
    # ident='' — don't advertise the server and version in the Server header.
    serve(application, host=host, port=port, threads=threads, ident='')


if __name__ == '__main__':
    _serve()
