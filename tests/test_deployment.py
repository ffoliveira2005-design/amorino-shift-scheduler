"""Tests for the deployment-readiness pass (September 2026).

Everything here guards a behaviour that only shows up on a real host, which is
exactly why each one was missing: on a laptop, over loopback, with one user,
none of it is visible.

  - SQLite runs in WAL, so a supervisor's write does not lock out every employee
    reading the schedule.
  - Responses are gzipped; /escala is ~195 KB of HTML and gets read on phones.
  - Static files carry a long cache, and the stylesheets carry a version so a
    deploy can still invalidate them.
  - /healthz answers without a session and discloses nothing.
  - ProxyFix is applied when, and only when, AMORINO_TRUST_PROXY says a proxy is
    there — otherwise the iCal link handed to employees says http://.
  - requirements.txt is bounded, and wsgi.py exposes a WSGI callable.
"""
import gzip
import os
import subprocess
import sys
import tempfile
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import (PROJECT_ROOT, app, client, supervisor_client,  # noqa: F401
                         employee_client)


# ───────────────────────────────────────────────────────────────────────────────
# SQLite concurrency
# ───────────────────────────────────────────────────────────────────────────────

def test_sqlite_runs_in_wal_mode(app):
    """The rollback journal takes a lock that excludes readers for the whole
    write. On a threaded WSGI server that means one supervisor saving the escala
    stalls everyone reading it, and past the busy timeout they get a 500."""
    from sqlalchemy import text
    with app.app.app_context():
        with app.db.engine.connect() as conn:
            assert conn.execute(text('PRAGMA journal_mode')).scalar().lower() == 'wal'
            # NORMAL is the durable-enough pairing for WAL; FULL costs an fsync
            # per commit for a guarantee a schedule app does not need.
            assert conn.execute(text('PRAGMA synchronous')).scalar() == 1
            assert conn.execute(text('PRAGMA foreign_keys')).scalar() == 1


def test_sqlite_busy_timeout_is_generous(app):
    """A write that has to wait should wait, not fail. The 5s default is short
    for a page that commits several rows behind a slow disk."""
    from sqlalchemy import text
    with app.app.app_context():
        with app.db.engine.connect() as conn:
            assert conn.execute(text('PRAGMA busy_timeout')).scalar() >= 30000


def test_pragmas_apply_to_every_new_connection(app):
    """PRAGMAs are per-connection and the pool opens one per thread, so setting
    them once at startup would leave every other thread on the defaults."""
    from sqlalchemy import text
    with app.app.app_context():
        for _ in range(3):
            conn = app.db.engine.connect()
            try:
                assert conn.execute(text('PRAGMA foreign_keys')).scalar() == 1
            finally:
                conn.close()


# ───────────────────────────────────────────────────────────────────────────────
# Compression
# ───────────────────────────────────────────────────────────────────────────────

def test_large_pages_are_gzipped(supervisor_client):
    r = supervisor_client.get('/escala', headers={'Accept-Encoding': 'gzip'})
    assert r.status_code == 200
    assert r.headers.get('Content-Encoding') == 'gzip'
    body = gzip.decompress(r.data)
    assert b'<!DOCTYPE html>' in body[:200]


def test_gzip_round_trips_to_the_identical_body(supervisor_client):
    """Compression must not change a byte of what the browser ends up with."""
    plain = supervisor_client.get('/escala', headers={'Accept-Encoding': 'identity'})
    zipped = supervisor_client.get('/escala', headers={'Accept-Encoding': 'gzip'})
    assert plain.headers.get('Content-Encoding') is None
    assert gzip.decompress(zipped.data) == plain.data


def test_gzip_is_a_real_saving(supervisor_client):
    plain = supervisor_client.get('/escala', headers={'Accept-Encoding': 'identity'})
    zipped = supervisor_client.get('/escala', headers={'Accept-Encoding': 'gzip'})
    assert len(zipped.data) < len(plain.data) * 0.4, (
        f'{len(plain.data)} -> {len(zipped.data)} bytes is not worth the CPU')


def test_no_gzip_when_the_client_does_not_ask(supervisor_client):
    r = supervisor_client.get('/escala', headers={'Accept-Encoding': 'identity'})
    assert r.headers.get('Content-Encoding') is None
    assert b'<!DOCTYPE html>' in r.data[:200]


def test_vary_is_set_even_when_not_compressing(supervisor_client):
    """A shared cache that stored the uncompressed page must not hand it to a
    client that asked for gzip, and vice versa."""
    for enc in ('gzip', 'identity'):
        r = supervisor_client.get('/escala', headers={'Accept-Encoding': enc})
        assert 'Accept-Encoding' in r.headers.get('Vary', ''), f'missing for {enc}'


def test_security_headers_survive_compression(supervisor_client):
    """The gzip step runs inside the same after_request that sets them."""
    r = supervisor_client.get('/escala', headers={'Accept-Encoding': 'gzip'})
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'
    assert r.headers.get('X-Frame-Options') == 'DENY'
    assert 'no-store' in r.headers.get('Cache-Control', '')
    assert 'Content-Security-Policy' in r.headers


def test_samesite_lax_still_holds_now_that_responses_are_compressed(app):
    """Compressing pages that carry a CSRF token is the BREACH trade-off, and
    SameSite=Lax is what makes it impractical here: the attack needs many
    authenticated cross-origin requests whose size it can watch, and the session
    cookie does not ride along on cross-site requests that are not a top-level
    navigation. Relaxing SameSite means revisiting the compression decision, so
    the two are pinned together. SECURITY.md records the same reasoning under
    "Não defendemos contra"."""
    assert app.app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'


def test_static_files_are_not_touched_by_the_gzip_step(supervisor_client):
    """send_file() streams from a handle; buffering it to compress would defeat
    that. The response must still be intact."""
    r = supervisor_client.get('/static/app.css', headers={'Accept-Encoding': 'gzip'})
    assert r.status_code == 200
    assert b'{' in r.data, 'stylesheet body did not survive'


# ───────────────────────────────────────────────────────────────────────────────
# Static caching
# ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('path', [
    '/static/app.css',
    '/static/fonts/inter-latin-400-normal.woff2',
])
def test_static_files_carry_a_long_cache(supervisor_client, path):
    """Flask's default is no-cache, which revalidates ~140 KB of CSS and fonts
    on every single navigation."""
    r = supervisor_client.get(path)
    cc = r.headers.get('Cache-Control', '')
    assert 'max-age=' in cc, f'{path} has no max-age: {cc!r}'
    assert int(cc.split('max-age=')[1].split(',')[0]) >= 86400 * 30


def test_asset_helper_versions_the_url(app):
    with app.app.test_request_context('/'):
        url = app.asset('app.css')
    assert url.startswith('/static/app.css?v='), url


def test_asset_version_changes_when_the_file_changes(app, tmp_path):
    """A year-long cache is only safe if a deploy can invalidate it."""
    target = os.path.join(app.app.static_folder, '_probe.css')
    try:
        with open(target, 'w', encoding='utf-8') as fh:
            fh.write('a{}')
        with app.app.test_request_context('/'):
            first = app.asset('_probe.css')
        with open(target, 'w', encoding='utf-8') as fh:
            fh.write('a{color:red}b{color:blue}c{color:green}')
        os.utime(target, (0, 0))          # force a different mtime, not just size
        with app.app.test_request_context('/'):
            second = app.asset('_probe.css')
    finally:
        if os.path.exists(target):
            os.remove(target)
    assert first != second, 'edited file kept the same cache-busting version'


def test_asset_helper_survives_a_missing_file(app):
    """Emit the plain URL so the 404 is the visible signal, rather than
    inventing a version for something that is not there."""
    with app.app.test_request_context('/'):
        assert app.asset('does-not-exist.css') == '/static/does-not-exist.css'


def test_template_versions_the_stylesheets_but_not_the_fonts(supervisor_client):
    """A preload only matches the later request if the URL is identical, and
    app-fonts.css asks for the faces by their plain paths. Versioning the
    preload and not the @font-face rule downloads every face twice."""
    html = supervisor_client.get('/escala').data.decode('utf-8', 'replace')
    assert '/static/app.css?v=' in html
    assert '/static/app-fonts.css?v=' in html
    assert '/static/fonts/inter-latin-700-normal.woff2"' in html, (
        'font preload should carry no version')


# ───────────────────────────────────────────────────────────────────────────────
# Health probe
# ───────────────────────────────────────────────────────────────────────────────

def test_healthz_answers_without_a_session(app):
    """A monitor holds no session; requiring one makes the probe useless."""
    r = app.app.test_client().get('/healthz')
    assert r.status_code == 200
    assert r.get_json() == {'ok': True}


def test_healthz_discloses_nothing(app):
    """It is unauthenticated, so it must not become a free information source:
    no version, no hostname, no row counts, no paths."""
    r = app.app.test_client().get('/healthz')
    assert set(r.get_json().keys()) == {'ok'}
    body = r.data.decode().lower()
    for leak in ('amorino.db', 'sqlite', 'python', 'flask', 'traceback', os.sep):
        assert leak.lower() not in body, f'/healthz leaks {leak!r}'


def test_healthz_is_not_cached(app):
    """A cached 200 would keep reporting healthy after the app stopped being."""
    r = app.app.test_client().get('/healthz')
    assert 'no-store' in r.headers.get('Cache-Control', '')


def test_the_set_of_unauthenticated_routes_is_the_documented_one(app):
    """/healthz is a new route that answers without a session, so it belongs on
    a list somebody reviews. This pins that list: adding another route that
    needs no login fails here until it is a deliberate, documented decision.
    SECURITY.md carries the same table under "Rotas sem autenticação"."""
    import inspect
    import re
    expected = {
        'static',              # app's own files
        'login',
        'enroll_access',       # private invitation + anonymous CSRF, no sigla-only claim
        'logout',              # ending a session cannot require a valid one
        'supervisor_login',    # back-compat redirect to /login
        'supervisor_logout',   # back-compat redirect to /logout
        'healthz',             # a monitor holds no session
        'ical_feed',           # calendar clients cannot send cookies; token is
        'ical_qr',             # the credential for both of these
    }
    src = inspect.getsource(app)
    open_endpoints = set()
    for rule in app.app.url_map.iter_rules():
        if rule.endpoint == 'static':
            open_endpoints.add('static')
            continue
        name = app.app.view_functions[rule.endpoint].__name__
        m = re.search(r'((?:^@[^\n]*\n)+)def %s\(' % re.escape(name), src, re.M)
        decorators = m.group(1) if m else ''
        if 'login_required' not in decorators and 'supervisor_required' not in decorators:
            open_endpoints.add(rule.endpoint)
    assert open_endpoints == expected, (
        f'unauthenticated routes changed.\n'
        f'  new:     {sorted(open_endpoints - expected)}\n'
        f'  removed: {sorted(expected - open_endpoints)}\n'
        f'Update SECURITY.md ("Rotas sem autenticação") and this list together.')


def test_healthz_reports_503_when_the_database_is_gone(app, monkeypatch):
    """The failure a TCP check cannot see: still accepting connections while
    every page 500s."""
    def boom(*a, **kw):
        raise RuntimeError('disk gone')
    monkeypatch.setattr(app.db.session, 'execute', boom)
    r = app.app.test_client().get('/healthz')
    assert r.status_code == 503
    assert r.get_json() == {'ok': False}


# ───────────────────────────────────────────────────────────────────────────────
# Reverse proxy
# ───────────────────────────────────────────────────────────────────────────────

def _cal_link_behind_proxy(trust_proxy: bool):
    """Boot the app in a subprocess, since ProxyFix is applied at import time,
    and ask it for the iCal URL it would hand an employee."""
    code = textwrap.dedent('''
        import os, tempfile
        os.environ['AMORINO_DB_PATH'] = os.path.join(
            tempfile.mkdtemp(prefix='amorino_proxy_'), 'a.db')
        import app
        c = app.app.test_client()
        with app.app.app_context():
            f = app.Funcionario.query.filter(app.Funcionario.sigla != 'ADMIN').first()
            sigla = f.sigla
        # A real login, so the session carries whatever the app actually needs.
        # /login is CSRF-exempt, so no token dance here. Same base_url as the
        # request below, or the session cookie is scoped to a different host and
        # never gets sent.
        BASE = 'http://127.0.0.1:8000'
        login = c.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'}, base_url=BASE)
        assert login.status_code == 302, 'supervisor login failed: %s' % login.status_code
        r = c.get('/api/cal-link?sigla=' + sigla,
                  base_url=BASE,
                  headers={'X-Forwarded-Proto': 'https',
                           'X-Forwarded-Host': 'escalas.example.pt',
                           'X-Forwarded-For': '203.0.113.9'})
        print('RESULT', r.status_code, (r.get_json() or {}).get('url'))
    ''')
    env = dict(os.environ)
    env.pop('AMORINO_TRUST_PROXY', None)
    env.pop('AMORINO_HTTPS', None)
    env.pop('AMORINO_SUPERVISOR_PIN', None)
    if trust_proxy:
        env['AMORINO_TRUST_PROXY'] = '1'
    p = subprocess.run([sys.executable, '-c', code], cwd=PROJECT_ROOT, env=env,
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    for line in p.stdout.splitlines():
        if line.startswith('RESULT'):
            _, status, url = line.split(' ', 2)
            return int(status), url
    raise AssertionError(f'subprocess produced no result:\n{p.stdout}\n{p.stderr}')


def test_proxy_headers_are_ignored_by_default():
    """X-Forwarded-* is forgeable by anyone who can reach the app directly, so
    trusting it must be a decision, not a default."""
    status, url = _cal_link_behind_proxy(trust_proxy=False)
    assert status == 200, url
    assert url.startswith('http://127.0.0.1:8000/'), url


def test_trusted_proxy_yields_https_subscription_links():
    """Behind a TLS-terminating proxy the app sees plain HTTP, so the iCal link
    an employee copies into their phone said http://. Calendar clients do not
    honour HSTS, so the feed token would go over the wire in clear on every
    poll."""
    status, url = _cal_link_behind_proxy(trust_proxy=True)
    assert status == 200, url
    assert url.startswith('https://escalas.example.pt/'), url


# ───────────────────────────────────────────────────────────────────────────────
# Packaging / entrypoint
# ───────────────────────────────────────────────────────────────────────────────

def test_wsgi_module_exposes_a_callable():
    """`gunicorn wsgi:application` has to find something to call."""
    sys.path.insert(0, PROJECT_ROOT)
    import wsgi
    assert callable(wsgi.application)
    assert wsgi.app is wsgi.application


def test_every_requirement_has_an_upper_bound():
    """Without one, the version running in production is decided by the date of
    the install rather than by anything that was tested."""
    path = os.path.join(PROJECT_ROOT, 'requirements.txt')
    with open(path, encoding='utf-8') as fh:
        lines = [ln.strip() for ln in fh
                 if ln.strip() and not ln.strip().startswith('#')]
    assert lines, 'requirements.txt has no requirements'
    for line in lines:
        assert '<' in line, f'{line!r} has no upper bound'


def test_a_production_server_is_declared():
    """app.py's development server is not for deployment, so the alternative
    has to be installable from the requirements file rather than remembered."""
    path = os.path.join(PROJECT_ROOT, 'requirements.txt')
    with open(path, encoding='utf-8') as fh:
        content = fh.read().lower()
    assert 'waitress' in content or 'gunicorn' in content
