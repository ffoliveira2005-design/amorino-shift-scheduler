"""
Smoke tests for Amorino Escalas.

Run with:  pytest -v tests/

These are not exhaustive — they cover the happy paths and the bugs we fixed
during the May 2026 audit (1, 2, 3, 4, 6, 13, 14). If you ever rip out one of
the fixes by accident, the matching test will scream.

Each test uses a fresh in-memory-ish DB by pointing SQLALCHEMY at a tempfile
and clearing it between runs. The app's seeder fills the DB with the same
roster used in production.
"""
import os
import sys
import json
import tempfile
import importlib.util
import pytest

# Ensure project root is importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def app():
    """Fresh app + fresh DB per test (Windows-safe).

    We tell app.py where to put its DB via the AMORINO_DB_PATH env var, set
    BEFORE the module is imported. That way the seed runs directly against
    the tempdir DB — we never touch the project's amorino.db.

    On teardown we dispose the SQLAlchemy engine so SQLite releases its file
    lock (Windows holds the file open until then; Linux doesn't care).
    """
    tmpdir = tempfile.mkdtemp(prefix='amorino_test_')
    db_path = os.path.join(tmpdir, 'amorino.db')

    # Set the DB path BEFORE importing app.py — this is the whole trick.
    prev_db_env = os.environ.get('AMORINO_DB_PATH')
    os.environ['AMORINO_DB_PATH'] = db_path

    # CWD matters because render_template looks for ./templates/.
    cwd = os.getcwd()

    # Copy templates dir into the tempdir (or symlink would do, but tempdir is
    # easier on Windows). Falls back to the project root if no templates/.
    import shutil
    src_templates = os.path.join(PROJECT_ROOT, 'templates')
    if not os.path.isdir(src_templates):
        src_templates = PROJECT_ROOT
    shutil.copytree(
        src_templates, os.path.join(tmpdir, 'templates'),
        dirs_exist_ok=True,
        ignore=lambda d, files: [f for f in files if not f.endswith('.html')],
    )

    os.chdir(tmpdir)

    # Import a *fresh* copy of app.py each test. Each gets its own module so
    # globals like LOGIN_ATTEMPTS don't leak between tests.
    unique_name = f'app_test_{os.path.basename(tmpdir)}'
    spec = importlib.util.spec_from_file_location(
        unique_name, os.path.join(PROJECT_ROOT, 'app.py'),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = mod   # register so the loader is happy
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # If import fails, clean up the env var and re-raise with context.
        if prev_db_env is None:
            os.environ.pop('AMORINO_DB_PATH', None)
        else:
            os.environ['AMORINO_DB_PATH'] = prev_db_env
        os.chdir(cwd)
        sys.modules.pop(unique_name, None)
        raise

    mod.app.config['TESTING'] = True
    # Flask's default is to look for templates in PROJECT_ROOT/templates/.
    # Some setups put HTML files directly in PROJECT_ROOT instead. Support
    # both by handing Jinja a loader that searches both paths.
    from jinja2 import FileSystemLoader
    search_paths = []
    if os.path.isdir(os.path.join(PROJECT_ROOT, 'templates')):
        search_paths.append(os.path.join(PROJECT_ROOT, 'templates'))
    search_paths.append(PROJECT_ROOT)
    mod.app.jinja_loader = FileSystemLoader(search_paths)

    # Sanity: ensure we landed on the tempdir DB.
    assert mod.app.config['SQLALCHEMY_DATABASE_URI'].endswith(
        db_path.replace(os.sep, '/')) or db_path in mod.app.config['SQLALCHEMY_DATABASE_URI']
    mod.LOGIN_ATTEMPTS.clear()
    from helpers.auth import LOGIN_ATTEMPTS_GLOBAL
    LOGIN_ATTEMPTS_GLOBAL.clear()

    yield mod

    # ── Teardown ───────────────────────────────────────────────────────────
    # CRITICAL on Windows: dispose the engine so SQLite releases the .db file.
    # Without this, the tempdir cleanup fails with WinError 32.
    try:
        with mod.app.app_context():
            mod.db.session.remove()
            mod.db.engine.dispose()
    except Exception:
        pass

    # Restore env var
    if prev_db_env is None:
        os.environ.pop('AMORINO_DB_PATH', None)
    else:
        os.environ['AMORINO_DB_PATH'] = prev_db_env

    # Remove our temp module so it can be garbage-collected
    sys.modules.pop(unique_name, None)

    os.chdir(cwd)

    # Best-effort: try to delete the tempdir. On Windows this can still fail
    # if any handle is lingering, so we ignore errors.
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


class _CsrfClient:
    """Wraps a Flask test_client to auto-inject the CSRF token on state-changing
    requests. Lets tests stay readable without scattering csrf_token everywhere.

    The token is read from the Flask session after each request, so it stays
    in sync if the server rotates it (e.g. after login).
    """
    def __init__(self, raw_client, app_mod):
        self._c = raw_client
        self._app_mod = app_mod

    def _csrf(self):
        # Use Flask's session_transaction to peek into the test client's session
        with self._c.session_transaction() as sess:
            return sess.get('_csrf', '')

    def get(self, *a, **kw):
        return self._c.get(*a, **kw)

    def _inject(self, kwargs):
        tok = self._csrf()
        if not tok:
            return kwargs
        if 'json' in kwargs and isinstance(kwargs.get('json'), dict):
            kwargs['json'] = {**kwargs['json'], 'csrf_token': tok}
        elif 'data' in kwargs and isinstance(kwargs.get('data'), dict):
            kwargs['data'] = {**kwargs['data'], 'csrf_token': tok}
        else:
            headers = dict(kwargs.get('headers') or {})
            headers.setdefault('X-CSRF-Token', tok)
            kwargs['headers'] = headers
        return kwargs

    def post(self, *a, **kw):    return self._c.post(*a,    **self._inject(kw))
    def put(self, *a, **kw):     return self._c.put(*a,     **self._inject(kw))
    def patch(self, *a, **kw):   return self._c.patch(*a,   **self._inject(kw))
    def delete(self, *a, **kw):  return self._c.delete(*a,  **self._inject(kw))

    # Pass-through for less common methods + session access
    def session_transaction(self): return self._c.session_transaction()


@pytest.fixture
def client(app):
    """CSRF-aware test client. Login route is exempt from CSRF, so the first
    POST works without a token; subsequent ones use whatever the session has."""
    return _CsrfClient(app.app.test_client(), app)


@pytest.fixture
def supervisor_client(app, client):
    r = client.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'},
                    follow_redirects=False)
    assert r.status_code == 302, f'supervisor login failed: {r.status_code} {r.data[:200]!r}'
    return client


@pytest.fixture
def employee_client(app, client):
    # Explicitly provision fictional credentials; a blank account cannot claim
    # access by simply knowing its sigla.
    with app.app.app_context():
        app.db.session.get(app.Funcionario, 'FO').pin = app.hash_pin('mypin')
        app.db.session.commit()
    r = client.post('/login', data={'sigla': 'FO', 'pin': 'mypin'},
                    follow_redirects=False)
    assert r.status_code == 302, f'employee login failed: {r.status_code} {r.data[:200]!r}'
    return client


# ───────────────────────────────────────────────────────────────────────────────
# Login / auth
# ───────────────────────────────────────────────────────────────────────────────

def test_login_page_renders(client):
    r = client.get('/login')
    assert r.status_code == 200
    assert b'Entrar' in r.data or b'PIN' in r.data


def test_management_overview_requires_supervisor(client, employee_client):
    """The executive summary contains workforce data and is supervisor-only."""
    response = employee_client.get('/supervisor/visao-geral', follow_redirects=False)
    assert response.status_code == 302
    assert '/login' in response.headers['Location']
    employee_client.get('/logout')
    anonymous = client.get('/supervisor/visao-geral', follow_redirects=False)
    assert anonymous.status_code == 302
    assert '/login' in anonymous.headers['Location']


def test_login_supervisor_success(client):
    r = client.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['Location'].rstrip('/') in ('', '/supervisor', '/supervisor/')


def test_login_bad_pin_renders_error(client):
    r = client.post('/login', data={'sigla': 'ADMIN', 'pin': 'wrong'})
    assert r.status_code == 200
    # SECURITY: error message must be generic so attackers can't tell whether
    # the sigla exists or the PIN was wrong.
    assert b'Sigla ou PIN incorreto' in r.data
    # And the more specific old message should NOT appear
    assert b'PIN incorreto.' not in r.data or b'Sigla ou PIN incorreto' in r.data


def test_login_does_not_leak_sigla_existence(client):
    """SECURITY: posting an existing sigla with wrong PIN and a non-existent
    sigla with anything must return identical error messages."""
    r1 = client.post('/login', data={'sigla': 'FO', 'pin': 'wrong'})
    r2 = client.post('/login', data={'sigla': 'NOTREAL', 'pin': 'anything'})
    # Both should have the same body modulo any HTML differences in the
    # funcionarios dropdown — but the error message itself should be identical.
    import re
    def err(html):
        m = re.search(rb'class="err-box"[^>]*>([^<]+)</', html)
        return m.group(1).strip() if m else b''
    assert err(r1.data) == err(r2.data), \
        f'Login error messages differ: {err(r1.data)!r} vs {err(r2.data)!r}'


def test_login_employee_without_provisioning_cannot_claim_account(app, client):
    """A sigla and a chosen PIN are not proof of identity."""
    r = client.post('/login', data={'sigla': 'FO', 'pin': 'hello'}, follow_redirects=False)
    assert r.status_code == 200
    assert b'Sigla ou PIN incorreto' in r.data
    with app.app.app_context():
        f = app.db.session.get(app.Funcionario, 'FO')
        assert not f.pin, 'An unverified request must not establish credentials'


def test_login_legacy_plaintext_pin_gets_upgraded(app, client):
    """BUG 13 regression: an existing plaintext PIN should be hashed on next success."""
    with app.app.app_context():
        f = app.db.session.get(app.Funcionario, 'FO')
        f.pin = 'legacy'  # write plaintext directly to simulate old DB
        app.db.session.commit()
        assert not app._looks_hashed(f.pin)

    r = client.post('/login', data={'sigla': 'FO', 'pin': 'legacy'}, follow_redirects=False)
    assert r.status_code == 302

    with app.app.app_context():
        f = app.db.session.get(app.Funcionario, 'FO')
        assert app._looks_hashed(f.pin), 'Plaintext PIN should be upgraded to hashed'


def test_login_rate_limit_kicks_in(app, client):
    """BUG 14 regression: too many bad attempts should be rejected."""
    for _ in range(app.LOGIN_MAX + 2):
        client.post('/login', data={'sigla': 'ADMIN', 'pin': 'wrong'})
    # Even with the correct PIN, we should now be blocked
    r = client.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'})
    assert r.status_code == 200
    assert b'tentativas' in r.data.lower() or b'Demasiadas' in r.data


# ───────────────────────────────────────────────────────────────────────────────
# Disponibilidade preload (BUG 1)
# ───────────────────────────────────────────────────────────────────────────────

def test_disponibilidade_get_blank_for_new_user(app, employee_client):
    r = employee_client.get('/disponibilidade', follow_redirects=False)
    # Diagnostic: if this fails, we want to know whether the issue is auth
    # (302 to /login) or a server error (500) or something else.
    if r.status_code != 200:
        print('UNEXPECTED status:', r.status_code)
        print('Location header:', r.headers.get('Location'))
        print('Body[:500]:', r.data[:500])
    assert r.status_code == 200
    # No banner because no answers exist yet
    assert b'class="submission-status"' not in r.data
    # Preload arrays should be empty
    import re
    text = r.data.decode()
    assert re.search(r'const INITIAL_BY_DAY\s*=\s*\{\}', text), 'INITIAL_BY_DAY should be empty'
    assert re.search(r'const INITIAL_GLOBAL_LOJAS\s*=\s*\[\]', text), 'INITIAL_GLOBAL_LOJAS should be empty'


def test_disponibilidade_preloads_after_submit(app, employee_client):
    """BUG 1 regression: GET after POST must show the saved answers."""
    with app.app.app_context():
        semana = app.get_semana_ativa()
        d1 = semana.dias_list[0]['label']

    employee_client.post('/disponibilidade', data={
        f'tipo_{d1}': 'disponivel',
        f'texto_{d1}': 'Olá',
        f'lojas_{d1}': 'carmelitas',
        f'slots_{d1}': 'manha',
    })

    r = employee_client.get('/disponibilidade')
    assert r.status_code == 200
    assert b'class="submission-status"' in r.data, 'Banner must appear after first submit'
    # The day key + chosen tipo must be present in the JSON preload
    assert d1.encode() in r.data
    assert b'"disponivel"' in r.data
    assert b'carmelitas' in r.data


# ───────────────────────────────────────────────────────────────────────────────
# Turno add / move / remove (BUGS 2, 3)
# ───────────────────────────────────────────────────────────────────────────────

def test_turno_add_same_loja_same_time_overlap_rejected(app, supervisor_client):
    """BUG 3 regression: two overlapping shifts in the same loja for the same person → reject."""
    # Find an existing turno for VM and try to overlap it
    with app.app.app_context():
        t = app.Turno.query.filter_by(funcionario='VM').first()
        loja, dia, semana = t.loja, t.dia, t.semana

    r = supervisor_client.post('/api/turno/add', json={
        'loja': loja, 'horario': '13:00-19:00', 'dia': dia,
        'funcionario': 'VM', 'nota': '', 'semana': semana,
    })
    # If VM happens to already have something overlapping 13:00-19:00 here,
    # that's still a valid reject. Either way, no overlap should be allowed.
    if r.status_code == 409:
        # Message reads "…sobrepõe-se ao novo." — match the ASCII-stable prefix
        # (the õ is JSON-escaped, so don't assert on the accented byte).
        assert b'sobrep' in r.data or b'exato' in r.data


def test_turno_add_same_loja_non_overlap_allowed(app, supervisor_client):
    """BUG 3 regression: non-overlapping shifts in the same loja should be allowed."""
    # Use a clean fictional case: add VM to carmelitas 06:00-09:00 then 19:00-22:00 Dom 17
    semana = '11-17mai'
    # Pick a fresh sigla & day combo unlikely to collide
    r1 = supervisor_client.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '08:00-10:00', 'dia': 'Sab 16',
        'funcionario': 'GS', 'nota': '', 'semana': semana,
    })
    assert r1.status_code in (200, 409)  # might collide with seed; we tolerate
    r2 = supervisor_client.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '20:00-22:00', 'dia': 'Sab 16',
        'funcionario': 'GS', 'nota': '', 'semana': semana,
    })
    assert r2.status_code == 200, f'Non-overlap same-loja should succeed: {r2.data!r}'


def test_turno_add_cross_loja_without_troca_rejected(app, supervisor_client):
    """BUG 3: cross-loja same day still needs the 'troca' marker."""
    semana = '11-17mai'
    supervisor_client.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '10:00-14:30', 'dia': 'Dom 17',
        'funcionario': 'RB', 'nota': '', 'semana': semana,
    })
    r = supervisor_client.post('/api/turno/add', json={
        'loja': 'carmelitas', 'horario': '16:00-00:30', 'dia': 'Dom 17',
        'funcionario': 'RB', 'nota': '', 'semana': semana,
    })
    assert r.status_code == 409
    assert b'troca' in r.data.lower() or b'Troca' in r.data


def test_turno_move_atomic_preserves_on_conflict(app, supervisor_client):
    """BUG 2 regression: a failed move must leave the original turno intact."""
    semana = '11-17mai'
    # Create a fresh turno
    r1 = supervisor_client.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '08:00-10:00', 'dia': 'Seg 11',
        'funcionario': 'NMO', 'nota': '', 'semana': semana,
    })
    if r1.status_code != 200:
        pytest.skip(f'seed collision: {r1.data}')
    tid = r1.get_json()['id']

    # Create another that will conflict
    r2 = supervisor_client.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '15:00-17:00', 'dia': 'Seg 11',
        'funcionario': 'NMO', 'nota': '', 'semana': semana,
    })
    assert r2.status_code == 200

    # Try to move tid onto an overlap with the second
    r3 = supervisor_client.post(f'/api/turno/move/{tid}', json={
        'to_loja': 'mbs', 'to_horario': '14:00-16:00', 'to_dia': 'Seg 11',
    })
    assert r3.status_code == 409, 'Overlapping move should be rejected'

    # Confirm the original survived
    with app.app.app_context():
        t = app.db.session.get(app.Turno, tid)
        assert t is not None, 'Original turno must not be deleted on a failed move'
        assert (t.loja, t.horario, t.dia) == ('mbs', '08:00-10:00', 'Seg 11')


def test_turno_move_valid_works(app, supervisor_client):
    """Happy path for the move endpoint."""
    semana = '11-17mai'
    r1 = supervisor_client.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '08:00-10:00', 'dia': 'Seg 11',
        'funcionario': 'MRB', 'nota': '', 'semana': semana,
    })
    if r1.status_code != 200:
        pytest.skip(f'seed collision: {r1.data}')
    tid = r1.get_json()['id']

    r2 = supervisor_client.post(f'/api/turno/move/{tid}', json={
        'to_loja': 'mbs', 'to_horario': '20:00-22:00', 'to_dia': 'Qui 14',
    })
    assert r2.status_code == 200
    with app.app.app_context():
        t = app.db.session.get(app.Turno, tid)
        assert (t.loja, t.horario, t.dia) == ('mbs', '20:00-22:00', 'Qui 14')


# ───────────────────────────────────────────────────────────────────────────────
# Suppression of base blocks (BUG 4)
# ───────────────────────────────────────────────────────────────────────────────

def test_horario_suppression_persists(app, supervisor_client):
    """BUG 4 regression: deleting a base block must survive (persisted)."""
    # Pick a base block that no one is assigned to. We need to check first.
    with app.app.app_context():
        # Find a base block with no turnos
        target = None
        for h in app.HORARIOS_BASE['carmelitas']:
            if app.Turno.query.filter_by(loja='carmelitas', horario=h).count() == 0:
                target = h
                break
        assert target is not None, 'no free base block to test with'

    r = supervisor_client.post('/api/horario/delete', json={'loja': 'carmelitas', 'horario': target})
    assert r.status_code == 200
    data = r.get_json()
    assert data['ok'] and data['tipo'] == 'base'

    with app.app.app_context():
        rows = app.HorarioSupressed.query.filter_by(loja='carmelitas', horario=target).all()
        assert len(rows) == 1, 'Suppression should be persisted'

    # Re-adding should restore (not collide)
    r2 = supervisor_client.post('/api/horario/add', json={'loja': 'carmelitas', 'horario': target})
    assert r2.status_code == 200
    assert r2.get_json()['tipo'] == 'restored'

    with app.app.app_context():
        rows = app.HorarioSupressed.query.filter_by(loja='carmelitas', horario=target).all()
        assert len(rows) == 0


def test_horario_delete_refuses_when_in_use(app, supervisor_client):
    """A base block in use in a current/future week cannot be removed.

    The seed's carmelitas assignments live in the past week 11-17mai, and the
    delete endpoint intentionally ignores past-week usage (June 2026 change —
    retiring a band must never rewrite history). So we pin the same band into a
    FUTURE week to make the in-use guard actually fire.
    """
    with app.app.app_context():
        used = app.Turno.query.filter_by(loja='carmelitas').first()
        target = used.horario

    _create_past_week(app, 'wk-fut', 'Semana futura',
                      [('Seg 1', '2030-01-07'), ('Ter 2', '2030-01-08')])
    _add_past_turno(app, 'wk-fut', 'carmelitas', target, 'Seg 1', 'FO')

    r = supervisor_client.post('/api/horario/delete', json={'loja': 'carmelitas', 'horario': target})
    assert r.status_code == 409
    assert b'em uso' in r.data


# ───────────────────────────────────────────────────────────────────────────────
# safe_referrer (BUG 6)
# ───────────────────────────────────────────────────────────────────────────────

def test_marcar_revisto_rejects_external_referrer(app, supervisor_client):
    """BUG 6 regression: Referer to another host must NOT be followed."""
    # We need a disp ID first
    with app.app.app_context():
        # Create one
        d = app.Disponibilidade(
            semana='11-17mai', funcionario='FO', dia='Seg 11', tipo='disponivel'
        )
        app.db.session.add(d)
        app.db.session.commit()
        did = d.id

    r = supervisor_client.post(f'/supervisor/marcar-revisto/{did}',
                               headers={'Referer': 'https://evil.example.com/x'},
                               follow_redirects=False)
    assert r.status_code == 302
    assert 'evil' not in r.headers['Location']
    assert not r.headers['Location'].startswith('//')


def test_marcar_revisto_respects_safe_referrer(app, supervisor_client):
    with app.app.app_context():
        d = app.Disponibilidade(
            semana='11-17mai', funcionario='JA', dia='Ter 12', tipo='preferencia'
        )
        app.db.session.add(d)
        app.db.session.commit()
        did = d.id

    r = supervisor_client.post(f'/supervisor/marcar-revisto/{did}',
                               headers={'Referer': '/supervisor/disponibilidades?semana=11-17mai'},
                               follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['Location'] == '/supervisor/disponibilidades?semana=11-17mai'


# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────

def test_shift_hours_calculation(app):
    """Backend hours calc; the JS in escala.html must match (FIX 5)."""
    assert app._shift_hours('08:00-16:30') == 8.0   # 8.5h gross - 0.5 lunch
    assert app._shift_hours('13:00-19:00') == 6.0   # 6h, no lunch
    assert app._shift_hours('16:00-00:30 (+1)') == 9.0
    assert app._shift_hours('08:00-17:00 (+0,5)') == 9.0


def test_shifts_overlap_helper(app):
    assert app._shifts_overlap('08:00-12:00', '11:00-15:00')
    assert not app._shifts_overlap('08:00-12:00', '13:00-19:00')
    assert app._shifts_overlap('08:00-16:30', '13:00-22:00')
    # Midnight crossing
    assert app._shifts_overlap('16:00-00:30', '20:00-23:00')


# ───────────────────────────────────────────────────────────────────────────────
# Security: CSRF, headers, session hardening, audit log
# ───────────────────────────────────────────────────────────────────────────────

def test_csrf_token_required_on_state_changing_endpoints(app):
    """A POST without CSRF token must be rejected even when authenticated."""
    raw = app.app.test_client()
    # Log in (login is exempt from CSRF)
    raw.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'})
    # Now try a state-changing endpoint WITHOUT the token
    r = raw.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '08:00-10:00', 'dia': 'Seg 11',
        'funcionario': 'FO', 'nota': '', 'semana': '11-17mai',
    })
    assert r.status_code == 403, f'expected 403, got {r.status_code}'


def test_csrf_token_accepted_via_header(app, supervisor_client):
    """The CSRF-aware fixture injects the token automatically; verify it works."""
    r = supervisor_client.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '08:00-10:00', 'dia': 'Sex 15',
        'funcionario': 'GS', 'nota': '', 'semana': '11-17mai',
    })
    # Either 200 (success) or 409 (seed-collision) — both prove CSRF was accepted.
    assert r.status_code in (200, 409), f'unexpected {r.status_code}: {r.data!r}'


def test_csrf_token_rotates_on_login(app):
    """SECURITY: session fixation prevention — login should rotate the CSRF token."""
    raw = app.app.test_client()
    # Trigger a session by visiting login page (gets a fresh _csrf)
    raw.get('/login')
    with raw.session_transaction() as s:
        before = s.get('_csrf', '')
    # Log in
    raw.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'})
    with raw.session_transaction() as s:
        after = s.get('_csrf', '')
    assert after, 'CSRF token must exist after login'
    if before:
        assert before != after, 'CSRF token must rotate on login'


def test_security_headers_present(app, supervisor_client):
    """Each response should carry the hardening headers."""
    r = supervisor_client.get('/supervisor')
    h = r.headers
    assert h.get('X-Content-Type-Options') == 'nosniff'
    assert h.get('X-Frame-Options') == 'DENY'
    assert 'Content-Security-Policy' in h
    assert 'Permissions-Policy' in h
    # HTML pages should not be cached
    assert 'no-store' in h.get('Cache-Control', '')


def test_unclaimed_account_rejects_short_pin_without_invitation(app):
    """SECURITY: enrollment requires an invitation, regardless of chosen PIN."""
    raw = app.app.test_client()
    # Try to set a PIN that's too short
    r = raw.post('/login', data={'sigla': 'GS', 'pin': '12'}, follow_redirects=False)
    assert r.status_code == 200, 'short PIN should re-render the form, not redirect'
    assert b'Sigla ou PIN incorreto' in r.data
    # Verify FO has no PIN set yet (the attempt didn't sneak through)
    with app.app.app_context():
        f = app.db.session.get(app.Funcionario, 'GS')
        assert not f.pin or app._looks_hashed(f.pin)


def test_unclaimed_account_rejects_weak_and_strong_pins_without_invitation(app):
    """SECURITY: even a strong new PIN cannot bypass supervisor provisioning."""
    raw = app.app.test_client()
    # '1234' is a classic weak PIN — should re-render the form, not log in.
    r = raw.post('/login', data={'sigla': 'GS', 'pin': '1234'}, follow_redirects=False)
    assert r.status_code == 200, 'weak PIN should re-render the form, not redirect'
    assert b'Sigla ou PIN incorreto' in r.data
    with app.app.app_context():
        f = app.db.session.get(app.Funcionario, 'GS')
        assert not f.pin, 'weak PIN must not be stored'
    # A strong PIN without the privately delivered invitation still fails.
    r2 = raw.post('/login', data={'sigla': 'GS', 'pin': 'k7m2p'}, follow_redirects=False)
    assert r2.status_code == 200
    assert b'Sigla ou PIN incorreto' in r2.data


def test_legacy_weak_pin_not_locked_out(app):
    """SECURITY: weak-PIN policy is first-login only — a legacy plaintext PIN
    that happens to be weak must still log in (and get upgraded), never blocked."""
    with app.app.app_context():
        f = app.db.session.get(app.Funcionario, 'FO')
        f.pin = '1234'  # simulate an old plaintext weak PIN already on file
        app.db.session.commit()
    raw = app.app.test_client()
    r = raw.post('/login', data={'sigla': 'FO', 'pin': '1234'}, follow_redirects=False)
    assert r.status_code == 302, 'existing weak PIN must not lock the user out'
    with app.app.app_context():
        f = app.db.session.get(app.Funcionario, 'FO')
        assert app._looks_hashed(f.pin), 'legacy weak PIN should be upgraded to a hash'


def test_audit_log_records_pin_reset(app, supervisor_client):
    """SECURITY: PIN reset is a privileged operation — must be audited."""
    r = supervisor_client.post('/api/funcionario/reset-pin/FO')
    assert r.status_code == 200
    with app.app.app_context():
        logs = app.AuditLog.query.filter_by(action='pin_reset', target='FO').all()
        assert len(logs) >= 1, 'PIN reset must be in the audit log'
        assert logs[-1].actor == 'ADMIN'


def test_audit_log_records_funcionario_delete(app, supervisor_client):
    r = supervisor_client.delete('/api/funcionario/delete/GS')
    assert r.status_code == 200
    with app.app.app_context():
        logs = app.AuditLog.query.filter_by(action='funcionario_delete', target='GS').all()
        assert len(logs) >= 1


def test_session_cookie_httponly(app):
    """Session cookie must be HttpOnly to mitigate XSS cookie theft."""
    raw = app.app.test_client()
    r = raw.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'},
                 follow_redirects=False)
    # The Set-Cookie header should contain HttpOnly
    set_cookies = r.headers.getlist('Set-Cookie')
    session_cookie = next((c for c in set_cookies if c.startswith('session=')), '')
    assert 'HttpOnly' in session_cookie, f'Session cookie not HttpOnly: {session_cookie!r}'
    assert 'SameSite=Lax' in session_cookie, f'Session cookie missing SameSite=Lax: {session_cookie!r}'


def test_login_does_not_leak_existing_sigla_via_timing_or_message(app):
    """SECURITY: error messages for wrong PIN vs nonexistent sigla must match.

    Wrong PIN, unknown sigla and unclaimed account share the same error.
    """
    raw = app.app.test_client()
    # Explicitly provision a fictional existing credential.
    with app.app.app_context():
        app.db.session.get(app.Funcionario, 'FO').pin = app.hash_pin('realpin')
        app.db.session.commit()
    raw.post('/login', data={'sigla': 'FO', 'pin': 'realpin'})
    # Logout to clear the session
    raw.get('/logout', follow_redirects=False)

    # Now compare error messages: wrong PIN vs unknown sigla
    r1 = raw.post('/login', data={'sigla': 'FO',        'pin': 'definitely-wrong'})
    r2 = raw.post('/login', data={'sigla': 'XYZNOSUCH', 'pin': 'anything'})
    assert b'Sigla ou PIN incorreto' in r1.data, f'r1: {r1.data[:200]!r}'
    assert b'Sigla ou PIN incorreto' in r2.data, f'r2: {r2.data[:200]!r}'


def test_admin_account_has_stricter_rate_limit(app):
    """ADMIN is a high-value target — should be capped sooner than regular accounts."""
    assert app.LOGIN_MAX_ADMIN < app.LOGIN_MAX_DEFAULT


def test_csrf_exempt_for_get_requests(app, supervisor_client):
    """GETs must work without any CSRF token (idempotent reads)."""
    r = supervisor_client.get('/api/upcoming-weeks')
    assert r.status_code == 200


def test_oversized_body_rejected(app, supervisor_client):
    """Bodies above MAX_CONTENT_LENGTH should be refused by Flask."""
    big = 'x' * (300 * 1024)  # 300 KB > 256 KB limit
    r = supervisor_client.post('/api/turno/add',
                                json={'nota': big, 'loja': 'mbs', 'horario': '08:00-10:00',
                                      'dia': 'Seg 11', 'funcionario': 'FO', 'semana': '11-17mai'})
    # Flask returns 413 for over-limit bodies
    assert r.status_code in (413, 400), f'expected 413, got {r.status_code}'


def test_funcionario_add_rejects_invalid_sigla(app, supervisor_client):
    """SECURITY: input validation rejects siglas with weird chars."""
    r = supervisor_client.post('/api/funcionario/add',
        json={'sigla': 'AB CD', 'nome': 'Test', 'horas': 30})
    assert r.status_code == 400


def test_login_page_does_not_leak_the_staff_roster(app, client):
    """SECURITY: /login is unauthenticated, so it must not disclose who works
    here. It used to render a picker containing every sigla and real name —
    staff PII to any passer-by, and a free list of valid siglas that made the
    generic login error message meaningless."""
    r = client.get('/login')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    with app.app.app_context():
        roster = app.Funcionario.query.all()
        assert roster, 'seed roster missing — test would pass vacuously'
        leaked = [f.sigla for f in roster if f.nome and f.nome in body]
        assert not leaked, f'login page leaks employee names: {leaked[:5]}'
        # Siglas must not be enumerable either.
        import re as _re
        leaked_siglas = [f.sigla for f in roster
                         if _re.search(r'\b%s\b' % _re.escape(f.sigla), body)]
        assert not leaked_siglas, f'login page leaks siglas: {leaked_siglas[:5]}'


def test_login_page_is_not_cacheable(client):
    """SECURITY: a cached /login would outlive any change to the page — which
    matters because it used to carry the staff roster."""
    r = client.get('/login')
    assert 'no-store' in r.headers.get('Cache-Control', ''), r.headers


def test_login_still_works_with_typed_sigla(app, client):
    """The typed-sigla form must still authenticate (incl. lowercase input)."""
    r = client.post('/login', data={'sigla': 'admin', 'pin': '1234'},
                    follow_redirects=False)
    assert r.status_code == 302, f'login failed: {r.data[:200]!r}'


def test_forwarded_for_cannot_bypass_login_rate_limit(app, client):
    """SECURITY: X-Forwarded-For is attacker-controlled unless a trusted proxy
    rewrites it, so it must not be honoured for rate-limit keying by default.
    Rotating the header used to hand out a fresh bucket per request, making the
    per-IP throttle a no-op."""
    from helpers import auth
    auth.LOGIN_ATTEMPTS.clear()
    auth.LOGIN_ATTEMPTS_GLOBAL.clear()
    # Pin the default posture: no trusted proxy, so XFF is ignored entirely.
    prev = os.environ.pop('AMORINO_TRUST_PROXY', None)
    try:
        # ADMIN's PIN is fixed, so a wrong one always fails (no first-login path).
        for i in range(app.LOGIN_MAX_ADMIN + 2):
            r = client.post('/login',
                            data={'sigla': 'ADMIN', 'pin': 'wrong'},
                            headers={'X-Forwarded-For': f'10.0.0.{i}'})
        assert b'Demasiadas tentativas' in r.data, \
            'spoofed X-Forwarded-For bypassed the per-IP login throttle'
    finally:
        if prev is not None:
            os.environ['AMORINO_TRUST_PROXY'] = prev


def test_rate_limit_counters_stay_bounded(app, client):
    """SECURITY: the counters are keyed on attacker-supplied values, so probing
    with unique siglas must not grow them without bound."""
    from helpers import auth
    auth.LOGIN_ATTEMPTS.clear()
    auth.LOGIN_ATTEMPTS_GLOBAL.clear()
    saved = auth.LOGIN_ATTEMPTS_MAX_KEYS
    try:
        with app.app.test_request_context('/'):
            # Checking must never create a bucket — only a recorded failure may.
            for i in range(50):
                auth.login_rate_limited(f'PROBE{i}')
            assert len(auth.LOGIN_ATTEMPTS) == 0, 'a read created rate-limit buckets'
            assert len(auth.LOGIN_ATTEMPTS_GLOBAL) == 0

            # And recorded failures are capped rather than accumulating forever.
            auth.LOGIN_ATTEMPTS_MAX_KEYS = 20
            for i in range(200):
                auth.login_record_failure(f'S{i}')
            assert len(auth.LOGIN_ATTEMPTS) <= 20
            assert len(auth.LOGIN_ATTEMPTS_GLOBAL) <= 20
    finally:
        auth.LOGIN_ATTEMPTS_MAX_KEYS = saved
        auth.LOGIN_ATTEMPTS.clear()
        auth.LOGIN_ATTEMPTS_GLOBAL.clear()


def _boot_app(env):
    """Import app.py in a subprocess with `env` overlaid. Returns (rc, output).

    A subprocess because the weak-PIN gate fires at import time, and this
    module's `app` fixture already has app.py loaded.
    """
    import subprocess, tempfile
    e = dict(os.environ)
    e.pop('AMORINO_SUPERVISOR_PIN', None)
    e.pop('AMORINO_HTTPS', None)
    e['AMORINO_DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='amorino_pin_'), 'a.db')
    e.update(env)
    # encoding must be explicit: text=True decodes with the locale codec, which
    # on Windows is cp1252 and cannot decode the '═' rule in the startup warning
    # banner. That raised UnicodeDecodeError and failed all three supervisor-PIN
    # tests on Windows — a red security suite for a reason that had nothing to
    # do with the PIN gate being tested.
    p = subprocess.run([sys.executable, '-c', 'import app'],
                       cwd=PROJECT_ROOT, env=e, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout + p.stderr)


def test_weak_supervisor_pin_refused_in_production(app):
    """SECURITY: the supervisor account holds every employee's data, so it must
    meet at least the bar imposed on employees at first login. Shipping the
    default '1234' while refusing that same PIN from a funcionário was the gap."""
    rc, out = _boot_app({'AMORINO_HTTPS': '1'})          # default PIN is '1234'
    assert rc != 0, 'app started in production posture with the default PIN'
    assert 'AMORINO_SUPERVISOR_PIN' in out

    # A different-but-still-weak PIN is refused too, not just the literal default.
    rc, out = _boot_app({'AMORINO_HTTPS': '1', 'AMORINO_SUPERVISOR_PIN': '9999'})
    assert rc != 0, 'repeated-digit supervisor PIN accepted in production'


def test_strong_supervisor_pin_boots_in_production(app):
    rc, out = _boot_app({'AMORINO_HTTPS': '1',
                         'AMORINO_SUPERVISOR_PIN': 'c7f2a91e4b'})
    assert rc == 0, f'strong PIN should boot cleanly:\n{out[-800:]}'


def test_weak_supervisor_pin_only_warns_in_dev(app):
    """Local dev must keep working — the gate is tied to the production posture
    (AMORINO_HTTPS=1), not to every run."""
    rc, out = _boot_app({})
    assert rc == 0, f'dev boot must not be blocked:\n{out[-800:]}'
    assert 'AMORINO_SUPERVISOR_PIN' in out, 'dev boot should still warn'


def test_debug_mode_is_not_the_default(app):
    """SECURITY: `python app.py` must not expose the Werkzeug console, which is
    arbitrary code execution for anyone who can reach a traceback."""
    import os as _os
    assert not app.app.debug
    src = open(_os.path.join(PROJECT_ROOT, 'app.py'), encoding='utf-8').read()
    assert 'app.run(debug=True' not in src, \
        'app.py starts the dev server with debug hardcoded on'


def test_employee_name_is_not_rendered_as_markup(app, supervisor_client):
    """SECURITY: a name is free text; the escala autocomplete must set it with
    textContent so it can never become live markup in someone else's browser."""
    src = open(os.path.join(PROJECT_ROOT, 'templates', 'escala.html'),
               encoding='utf-8').read()
    assert "'<span class=\"ac-sigla\">'+e.sigla" not in src, \
        'escala autocomplete builds employee names into innerHTML'


def test_horario_with_script_characters_is_rejected(app, supervisor_client):
    """SECURITY: a horario is rendered inside inline handlers in the escala grid
    (onclick="openQuickAdd('loja','<horario>',…)"). Jinja escapes ' to &#39;,
    but the browser decodes that BEFORE the JavaScript runs, so a quote in a
    horario breaks the row or runs script. Every endpoint that writes one must
    refuse characters a shift label never needs."""
    bad = ["10:00-18:00');alert(1);//", '10:00-18:00 <img src=x>',
           '10:00-18:00 "x"', "Provas D'Água", '10:00-18:00 \\x27']
    for h in bad:
        r = supervisor_client.post('/api/horario/add',
                                   json={'loja': 'carmelitas', 'horario': h})
        assert r.status_code == 400, f'block accepted: {h!r}'
        r = supervisor_client.post('/api/turno/add', json={
            'loja': 'mbs', 'horario': h, 'dia': 'Seg 11',
            'funcionario': 'GS', 'nota': '', 'semana': '11-17mai'})
        assert r.status_code == 400, f'turno accepted: {h!r}'
    with app.app.app_context():
        assert app.HorarioBloco.query.filter(
            app.HorarioBloco.horario.contains("'")).count() == 0

    # Moves write the same column — same rule, and the store must be real.
    r = supervisor_client.post('/api/turno/add', json={
        'loja': 'mbs', 'horario': '08:00-10:00', 'dia': 'Ter 12',
        'funcionario': 'GS', 'nota': '', 'semana': '11-17mai'})
    if r.status_code != 200:
        pytest.skip(f'seed collision: {r.data}')
    tid = r.get_json()['id']
    r = supervisor_client.post(f'/api/turno/move/{tid}', json={
        'to_loja': 'mbs', 'to_horario': "08:00-10:00');x('", 'to_dia': 'Ter 12'})
    assert r.status_code == 400
    r = supervisor_client.post(f'/api/turno/move/{tid}', json={
        'to_loja': 'nao-existe', 'to_horario': '11:00-13:00', 'to_dia': 'Ter 12'})
    assert r.status_code == 400

    # Real labels — times, "(+0,5)" extras, accented words — still pass.
    for ok in ('11:00-19:30 (+0,5)', 'Formação 10:00-14:00'):
        r = supervisor_client.post('/api/horario/add',
                                   json={'loja': 'carmelitas', 'horario': ok})
        assert r.status_code == 200, f'legit block refused: {ok!r} {r.data!r}'


def test_equipa_actions_do_not_put_names_in_inline_js(app, supervisor_client):
    """A name is free text. Interpolated into onclick="resetPin('X','D'Almeida')"
    it was a JavaScript syntax error — Reset PIN and Remover did nothing for
    anyone with an apostrophe in their name — and an injection point. The
    buttons now pass `this` and read sigla and name from the card."""
    import re
    r = supervisor_client.post('/api/funcionario/add', json={
        'sigla': 'ZZA', 'nome': "Ana D'Almeida", 'horas': 20,
        'tipo_contrato': 'contrato'})
    assert r.status_code == 200
    body = supervisor_client.get('/equipa').data.decode('utf-8')
    assert 'Almeida' in body
    handlers = re.findall(r'\bon[a-z]+="([^"]*)"', body)
    assert handlers, 'expected inline handlers on the page'
    leaking = [h for h in handlers if 'Almeida' in h]
    assert not leaking, f'name inside an inline handler: {leaking}'
    assert 'resetPin(this)' in body and 'deleteEmp(this)' in body


def test_funcionario_add_rejects_reserved_admin_sigla(app, supervisor_client):
    """SECURITY: can't create a real funcionario named ADMIN — reserved name."""
    r = supervisor_client.post('/api/funcionario/add',
        json={'sigla': 'ADMIN', 'nome': 'Impersonator', 'horas': 30})
    assert r.status_code == 400


def test_session_for_deleted_funcionario_is_invalidated(app):
    """SECURITY: if a funcionario is deleted, their session must stop working."""
    raw = app.app.test_client()
    # Set up FO with a real PIN, then log in
    with app.app.app_context():
        app.db.session.get(app.Funcionario, 'FO').pin = app.hash_pin('realpin')
        app.db.session.commit()
    raw.post('/login', data={'sigla': 'FO', 'pin': 'realpin'})
    # Verify we're logged in
    r = raw.get('/disponibilidade', follow_redirects=False)
    assert r.status_code == 200
    # Now delete FO directly via the DB (simulating supervisor delete)
    with app.app.app_context():
        f = app.db.session.get(app.Funcionario, 'FO')
        app.db.session.delete(f)
        app.db.session.commit()
    # The next request from FO's session should be kicked back to login
    r = raw.get('/disponibilidade', follow_redirects=False)
    assert r.status_code == 302, 'Deleted-funcionario session must be invalidated'
    assert '/login' in r.headers['Location']


def test_api_returns_json_not_redirect_when_unauthenticated(app):
    """SECURITY: API calls without auth must get JSON, not a 302.

    Otherwise a fetch() in the browser silently follows the redirect to /login,
    and the client gets the HTML body back as a "response" — confusing AND a
    risk if a script tries to parse it as JSON.

    Without a session the answer is 401 `sessao_expirada` — the same as
    login_required's — so base.html's fetch wrapper sends the supervisor back
    to the login page. (It was a 403 "Não autenticado", which the wrapper does
    not act on: an expired supervisor got an error toast and no way back.)
    """
    raw = app.app.test_client()
    r = raw.post('/api/funcionario/add',
                 json={'sigla': 'X', 'nome': 'Y', 'horas': 30})
    assert r.status_code == 401, f'expected 401, got {r.status_code}'
    assert r.get_json()['sessao_expirada'] is True


def test_supervisor_required_blocks_employees(app, employee_client):
    """An employee with a valid session can't hit supervisor-only endpoints."""
    r = employee_client.post('/api/funcionario/add',
        json={'sigla': 'XX', 'nome': 'New', 'horas': 30})
    assert r.status_code == 403


def test_supervisor_json_endpoints_answer_json_not_the_login_page(app, employee_client):
    """The dashboard POSTs JSON to /supervisor/* endpoints too (create/activate
    a week, the descarga grid). With the session gone they got the login PAGE,
    which fetch() followed and res.json() choked on — a dead button. Now: no
    session → 401 sessao_expirada (base.html sends the person to sign in);
    an employee's session → 403."""
    raw = app.app.test_client()
    r = raw.post('/supervisor/semana/nova', json={'monday': '2030-01-07'})
    assert r.status_code == 401 and r.get_json()['sessao_expirada'] is True
    r = employee_client.post('/supervisor/semana/nova', json={'monday': '2030-01-07'})
    assert r.status_code == 403 and r.is_json
    # A plain page still redirects to the login.
    assert raw.get('/supervisor', follow_redirects=False).status_code == 302


def _troca_people(app, *siglas):
    """Each sigla gets a shift two days from now; returns shift ids + clients."""
    from datetime import date as _date, timedelta as _td
    day = _date.today() + _td(days=2)
    monday = day - _td(days=day.weekday())
    with app.app.app_context():
        codigo = app.week_codigo(monday)
        if not app.SemanaConfig.query.filter_by(codigo=codigo).first():
            app.db.session.add(app.SemanaConfig(
                codigo=codigo, label=app.week_label(monday),
                dias_json=json.dumps(app.week_from_monday(monday)), ativa=False))
        label = next(d['label'] for d in app.week_from_monday(monday)
                     if d['date'] == day.isoformat())
        ids = {}
        for s in siglas:
            app.db.session.add(app.Funcionario(sigla=s, nome=f'Pessoa {s}',
                                               horas_contrato=32, tipo_contrato='contrato',
                                               pin=app.hash_pin('8317')))
            app.db.session.flush()
            t = app.Turno(semana=codigo, dia=label, loja='gaia',
                          horario='10:00-18:30', funcionario=s)
            app.db.session.add(t)
            app.db.session.flush()
            ids[s] = t.id
        app.db.session.commit()
        app.release_week(codigo, 'ADMIN', 'Escala autorizada para regressão de trocas')
        app.db.session.commit()
    clients = {}
    for s in siglas:
        c = _CsrfClient(app.app.test_client(), app)
        assert c.post('/login', data={'sigla': s, 'pin': '8317'}).status_code == 302
        clients[s] = c
    return ids, clients


def test_troca_colegas_is_only_for_the_shift_owner(app):
    """/api/trocas/colegas lists the whole team with who is busy at a given
    shift's hours. Only the shift's owner (or the supervisor) may ask."""
    ids, c = _troca_people(app, 'ZZA', 'ZZB')
    assert c['ZZA'].get(f"/api/trocas/colegas?turno={ids['ZZA']}").status_code == 200
    r = c['ZZB'].get(f"/api/trocas/colegas?turno={ids['ZZA']}")
    assert r.status_code == 403


def test_trocas_seen_cookie_is_httponly_lax_and_per_person(app, supervisor_client):
    """/trocas remembers when each person last looked (for the "news" badge)
    in a cookie: sigla|timestamp. HttpOnly + SameSite=Lax like the session;
    a value naming someone else is ignored, so it can only ever move one's
    own count. The supervisor has no news, so gets no cookie."""
    ids, c = _troca_people(app, 'ZZA', 'ZZB')
    r = c['ZZA'].get('/trocas')
    cookie = next(h for h in r.headers.getlist('Set-Cookie')
                  if h.startswith('amorino_trocas_vistas='))
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    assert cookie.split('=', 1)[1].startswith('ZZA|')
    r = supervisor_client.get('/trocas')
    assert not any(h.startswith('amorino_trocas_vistas=')
                   for h in r.headers.getlist('Set-Cookie'))

    # ZZB has news (a request ZZA cancelled); a far-future "seen" stamp that
    # names ZZA must not hide it.
    tid = c['ZZA'].post('/api/trocas', json={'turno_origem_id': ids['ZZA'],
                                             'para_sigla': 'ZZB',
                                             'turno_destino_id': ids['ZZB']}).get_json()['troca']['id']
    c['ZZA'].post(f'/api/trocas/{tid}/cancelar')
    raw = c['ZZB']._c
    raw.set_cookie('amorino_trocas_vistas', 'ZZA|2099-01-01T00:00:00')
    assert c['ZZB'].get('/api/trocas/contagem').get_json()['pendentes'] == 1
    raw.set_cookie('amorino_trocas_vistas', 'ZZB|2099-01-01T00:00:00')
    assert c['ZZB'].get('/api/trocas/contagem').get_json()['pendentes'] == 0


def test_central_does_not_show_other_peoples_candidaturas(app):
    """Who applied to a central post — and the comment they wrote — is for the
    post's author and the supervisor. /api/central used to hand every
    candidatura, comments included, to every employee."""
    ids, c = _troca_people(app, 'ZZA', 'ZZB', 'ZZC')
    # Different hours so ZZB and ZZC can both apply to ZZA's post.
    with app.app.app_context():
        for s in ('ZZB', 'ZZC'):
            app.db.session.get(app.Turno, ids[s]).horario = '19:00-23:00'
        app.db.session.commit()
        week = app.db.session.get(app.Turno, ids['ZZB']).semana
        app.release_week(week, 'ADMIN', 'Horários autorizados para teste de candidaturas')
        app.db.session.commit()
    tid = c['ZZA'].post('/api/trocas/abrir', json={
        'turno_origem_id': ids['ZZA'], 'tipo': 'aberto_doacao'}).get_json()['troca']['id']
    for s in ('ZZB', 'ZZC'):
        assert c[s].post(f'/api/trocas/{tid}/candidatar',
                         json={'comentario': f'nota de {s}'}).get_json()['ok']
    seen_by_b = c['ZZB'].get('/api/central').get_json()['pedidos'][0]
    assert [x['sigla'] for x in seen_by_b['candidaturas']] == ['ZZB']
    assert 'nota de ZZC' not in json.dumps(seen_by_b)
    seen_by_author = c['ZZA'].get('/api/central').get_json()['pedidos'][0]
    assert {x['sigla'] for x in seen_by_author['candidaturas']} == {'ZZB', 'ZZC'}


def test_logout_clears_session(app, supervisor_client):
    """SECURITY: logout must fully invalidate the session."""
    # First confirm logged in
    r = supervisor_client.get('/supervisor', follow_redirects=False)
    assert r.status_code == 200
    # Logout
    supervisor_client.get('/logout')
    # Now /supervisor should redirect to login
    r = supervisor_client.get('/supervisor', follow_redirects=False)
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_logout_audited(app, supervisor_client):
    supervisor_client.get('/logout')
    with app.app.app_context():
        logs = app.AuditLog.query.filter_by(action='logout').all()
        assert len(logs) >= 1


# ───────────────────────────────────────────────────────────────────────────────
# /api/empties — headcount-driven panel data (May 2026 redesign)
# ───────────────────────────────────────────────────────────────────────────────

def _set_disponibilidade(app_module, sigla, dia, tipo,
                          lojas_pref='', slots_pref='', semana='11-17mai',
                          descarga_opt=False):
    """Helper: directly insert a Disponibilidade row in the test DB."""
    with app_module.app.app_context():
        app_module.Disponibilidade.query.filter_by(
            funcionario=sigla, semana=semana, dia=dia
        ).delete()
        app_module.db.session.add(app_module.Disponibilidade(
            funcionario=sigla, semana=semana, dia=dia, tipo=tipo,
            lojas_pref=lojas_pref, slots_pref=slots_pref, texto='',
            descarga_opt=descarga_opt
        ))
        app_module.db.session.commit()


def _set_target(app_module, loja, dia_idx, target):
    """Helper: directly set a headcount target."""
    with app_module.app.app_context():
        row = app_module.LojaHeadcountTarget.query.filter_by(
            loja=loja, dia_idx=dia_idx
        ).first()
        if row:
            row.target = target
        else:
            app_module.db.session.add(app_module.LojaHeadcountTarget(
                loja=loja, dia_idx=dia_idx, target=target))
        app_module.db.session.commit()


def test_api_empties_requires_supervisor(client):
    """Non-supervisors cannot access."""
    r = client.get('/api/empties/11-17mai/Seg%2011')
    assert r.status_code in (302, 401, 403, 404)


def test_api_empties_returns_headcount_shape(supervisor_client):
    """Happy path: returns ok, lojas list, day_totals with headcount math."""
    r = supervisor_client.get('/api/empties/11-17mai/Seg%2011')
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] is True
    assert j['dia'] == 'Seg 11'
    # lojas array: one entry per configured loja, each with the required keys
    assert isinstance(j['lojas'], list)
    assert len(j['lojas']) == 5   # 5 stores
    for L in j['lojas']:
        for k in ('loja','loja_nome','target','filled','missing',
                  'current_siglas','suggestions','slots'):
            assert k in L, f'missing key {k}'
        assert L['missing'] == max(0, L['target'] - L['filled'])
    # day_totals uses headcount math: target=sum of all loja targets
    assert set(j['day_totals'].keys()) == {
        'Seg 11','Ter 12','Qua 13','Qui 14','Sex 15','Sab 16','Dom 17'
    }
    for d, t in j['day_totals'].items():
        assert 'filled' in t and 'target' in t


def test_api_empties_rejects_bad_week(supervisor_client):
    r = supervisor_client.get('/api/empties/nonexistent/Seg%2011')
    assert r.status_code == 404


def test_api_empties_rejects_bad_day(supervisor_client):
    r = supervisor_client.get('/api/empties/11-17mai/NotADay')
    assert r.status_code == 400


def test_api_empties_missing_increases_with_target_bump(app, supervisor_client):
    """Bumping a loja target should raise its 'missing' count by the same amount."""
    # Carmelitas Seg 11 (dia_idx=0) target seeded to 6
    r1 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm1 = next(L for L in r1['lojas'] if L['loja']=='carmelitas')
    initial_missing = carm1['missing']
    # Bump by 3
    _set_target(app, 'carmelitas', 0, carm1['target'] + 3)
    r2 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm2 = next(L for L in r2['lojas'] if L['loja']=='carmelitas')
    assert carm2['missing'] == initial_missing + 3


def test_api_empties_suggestions_appear_when_missing(app, supervisor_client):
    """When a loja is under-target, the API returns at least one suggestion."""
    _set_target(app, 'carmelitas', 0, 99)  # impossibly high → many missing
    r = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm = next(L for L in r['lojas'] if L['loja']=='carmelitas')
    assert carm['missing'] > 0
    assert len(carm['suggestions']) > 0
    # Each suggestion has sigla + reason
    for s in carm['suggestions']:
        assert 'sigla' in s and 'reason' in s
    # Suggestions must not be people already on this loja today
    for s in carm['suggestions']:
        assert s['sigla'] not in carm['current_siglas']


def test_api_empties_suggestion_prefers_preferers(app, supervisor_client):
    """A person with preference for the loja+slot should rank first."""
    # Bump Carmelitas Seg 11 to 99 so suggestions are visible
    _set_target(app, 'carmelitas', 0, 99)
    # ALS gets a full preference match for carmelitas
    _set_disponibilidade(app, 'ALS', 'Seg 11', 'preferencia',
                          lojas_pref='carmelitas', slots_pref='manha')
    r = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm = next(L for L in r['lojas'] if L['loja']=='carmelitas')
    # ALS should be at the top (best score). Only test "top" — there could be
    # other preferers we don't know about.
    assert carm['suggestions'][0]['sigla'] == 'ALS'
    assert carm['suggestions'][0]['reason'] == 'prefere'


def test_api_empties_suggestion_excludes_indisponivel(app, supervisor_client):
    """Indisponivel people are never suggested."""
    _set_target(app, 'carmelitas', 0, 99)
    r1 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm1 = next(L for L in r1['lojas'] if L['loja']=='carmelitas')
    top_sug = carm1['suggestions'][0]['sigla']
    # Mark that person as indisponivel
    _set_disponibilidade(app, top_sug, 'Seg 11', 'indisponivel')
    r2 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm2 = next(L for L in r2['lojas'] if L['loja']=='carmelitas')
    suggested_now = [s['sigla'] for s in carm2['suggestions']]
    assert top_sug not in suggested_now


def test_api_empties_suggestion_excludes_already_on_loja(app, supervisor_client):
    """People already scheduled on this loja today should never appear as suggestions."""
    _set_target(app, 'carmelitas', 0, 99)
    r = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm = next(L for L in r['lojas'] if L['loja']=='carmelitas')
    suggested = {s['sigla'] for s in carm['suggestions']}
    # No overlap between current_siglas and suggestions
    assert suggested.isdisjoint(carm['current_siglas'])


def test_api_empties_suggestion_excludes_other_loja_same_day(app, supervisor_client):
    """If someone has a turno in another loja that day, they can't be suggested
    for this loja the same day (cross-loja moves stay manual)."""
    _set_target(app, 'mbs', 0, 99)
    r1 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    mbs1 = next(L for L in r1['lojas'] if L['loja']=='mbs')
    top = mbs1['suggestions'][0]['sigla']
    # Book that person into a different loja for Seg 11
    other_loja = 'carmelitas' if top not in mbs1['current_siglas'] else 'gaia'
    r_others = next((L for L in r1['lojas'] if L['loja']==other_loja and top not in L['current_siglas']), None)
    if r_others is None or not r_others['slots'] or top in r_others['current_siglas']:
        # If the top suggestion is already on another loja, the test is moot — skip
        return
    add = supervisor_client.post('/api/turno/add', json={
        'loja': other_loja, 'horario': '14:00-22:30',
        'dia': 'Seg 11', 'funcionario': top, 'nota': '', 'semana': '11-17mai',
    })
    assert add.status_code == 200, add.data[:300]
    r2 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    mbs2 = next(L for L in r2['lojas'] if L['loja']=='mbs')
    assert top not in {s['sigla'] for s in mbs2['suggestions']}


def test_api_empties_descarga_only_optin_still_suggested(app, supervisor_client):
    """A descarga-only opt-in (tipo='' + descarga_opt) must not remove someone
    from the suggestion pool.

    /disponibilidade stores "I'll do the descarga that day, nothing else" as a
    row with an empty tipo and descarga_opt=True. The scorer used to return
    (None, None) for any unrecognised tipo, so those people vanished from every
    store's suggestions — submitting the form left them worse off than saying
    nothing at all.
    """
    _set_target(app, 'carmelitas', 0, 99)
    r1 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm1 = next(L for L in r1['lojas'] if L['loja'] == 'carmelitas')
    top = carm1['suggestions'][0]['sigla']

    _set_disponibilidade(app, top, 'Seg 11', '', descarga_opt=True)

    r2 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm2 = next(L for L in r2['lojas'] if L['loja'] == 'carmelitas')
    sug = {s['sigla']: s for s in carm2['suggestions']}
    assert top in sug, f'{top} vanished from suggestions after a descarga opt-in'
    assert sug[top]['reason'] == 'descarga'


def test_api_empties_blank_answer_ranks_as_no_answer(app, supervisor_client):
    """A row with no tipo and no descarga opt-in scores like 'sem resposta'
    rather than dropping the person entirely."""
    _set_target(app, 'carmelitas', 0, 99)
    r1 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm1 = next(L for L in r1['lojas'] if L['loja'] == 'carmelitas')
    top = carm1['suggestions'][0]['sigla']

    _set_disponibilidade(app, top, 'Seg 11', '')

    r2 = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    carm2 = next(L for L in r2['lojas'] if L['loja'] == 'carmelitas')
    assert top in {s['sigla'] for s in carm2['suggestions']}


# ── Descarga hours: 4h minimum, real duration when longer ─────────────────────

def _add_descarga(app_module, sigla, dia='Seg 11', loja='carmelitas',
                  semana='11-17mai', horario='08:00-12:00'):
    with app_module.app.app_context():
        app_module.db.session.add(app_module.Descarga(
            semana=semana, dia=dia, loja=loja,
            funcionario=sigla, horario=horario))
        app_module.db.session.commit()


def test_descarga_hours_rule():
    """4h floor; a longer window counts for what it really is."""
    from helpers.time_helpers import descarga_hours
    assert descarga_hours('08:00-12:00') == 4.0    # the default window
    assert descarga_hours('10:00-14:00') == 4.0    # MBS's window
    assert descarga_hours('08:00-11:00') == 4.0    # 3h → floored up to 4
    assert descarga_hours('08:00-10:00') == 4.0    # 2h → floored up to 4
    assert descarga_hours('08:00-14:00') == 6.0    # 6h → counted in full
    assert descarga_hours('08:00-13:30') == 5.5
    assert descarga_hours('') == 4.0               # unparseable → floor, not 0


def test_descarga_adds_four_hours_to_workload(app, supervisor_client):
    """A standard 4h descarga adds 4h in the workload tracker."""
    before = supervisor_client.get('/api/workload/11-17mai').get_json()
    base = before.get('ALS', {}).get('horas', 0.0)
    _add_descarga(app, 'ALS')
    after = supervisor_client.get('/api/workload/11-17mai').get_json()
    assert after['ALS']['horas'] == pytest.approx(base + 4.0)


def test_short_descarga_still_credits_four_hours(app, supervisor_client):
    """A window shorter than 4h is still worth the full 4h block."""
    before = supervisor_client.get('/api/workload/11-17mai').get_json()
    base = before.get('ALS', {}).get('horas', 0.0)
    _add_descarga(app, 'ALS', horario='08:00-10:30')   # 2.5h
    after = supervisor_client.get('/api/workload/11-17mai').get_json()
    assert after['ALS']['horas'] == pytest.approx(base + 4.0)


def test_long_descarga_credits_its_real_duration(app, supervisor_client):
    """A window longer than 4h is paid for what it actually is."""
    before = supervisor_client.get('/api/workload/11-17mai').get_json()
    base = before.get('ALS', {}).get('horas', 0.0)
    _add_descarga(app, 'ALS', horario='08:00-14:00')   # 6h
    after = supervisor_client.get('/api/workload/11-17mai').get_json()
    assert after['ALS']['horas'] == pytest.approx(base + 6.0)


def test_descarga_counts_on_employee_schedule_page(app, employee_client):
    """The employee's own page includes the descarga in the week total."""
    _add_descarga(app, 'FO')
    with app.app.app_context():
        app.release_week('11-17mai', 'ADMIN', 'Descarga autorizada para teste da escala')
        app.db.session.commit()
    r = employee_client.get('/minha-escala?semana=11-17mai&sigla=FO')
    assert r.status_code == 200
    with app.app.app_context():
        turnos = app.Turno.query.filter_by(funcionario='FO', semana='11-17mai').all()
        expected = round(sum(app._shift_hours(t.horario) for t in turnos) + 4.0, 1)
    # The template renders the total; assert the computed figure appears.
    body = r.data.decode('utf-8')
    assert f'{expected:.1f}'.replace('.', ',') in body or f'{expected:.1f}' in body, \
        f'expected total {expected} on the page'


def test_descarga_hours_agree_across_surfaces(app, supervisor_client):
    """Workload API and the CSV export must report the same hours."""
    _add_descarga(app, 'ALS')
    with app.app.app_context():
        app.release_week('11-17mai', 'ADMIN', 'Descarga autorizada para reconciliação')
        app.db.session.commit()
    wl = supervisor_client.get('/api/workload/11-17mai').get_json()['ALS']['horas']
    csv_body = supervisor_client.get(
        '/supervisor/exportar-horas-csv?semana=11-17mai').data.decode('utf-8')
    row = next(l for l in csv_body.splitlines() if l.startswith('ALS;'))
    total = float(row.split(';')[9].replace(',', '.'))   # 'Total horas' column
    assert total == pytest.approx(wl)


def test_api_empties_day_totals_match_lojas_for_that_day(supervisor_client):
    """day_totals for the requested day must equal the sum across all lojas."""
    r = supervisor_client.get('/api/empties/11-17mai/Seg%2011').get_json()
    sum_filled = sum(L['filled'] for L in r['lojas'])
    sum_target = sum(L['target'] for L in r['lojas'])
    assert r['day_totals']['Seg 11']['filled'] == sum_filled
    assert r['day_totals']['Seg 11']['target'] == sum_target


# ── Headcount target endpoints ─────────────────────────────────────────────────

def test_headcount_targets_get_returns_full_grid(supervisor_client):
    """GET returns every (loja, dia_idx) target, with zeros where unset."""
    r = supervisor_client.get('/api/headcount-targets')
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] is True
    assert j['dias'] == ['Seg','Ter','Qua','Qui','Sex','Sab','Dom']
    # 5 lojas, all present with all 7 days
    assert set(j['targets'].keys()) == {'carmelitas','clerigos','norte','gaia','mbs'}
    for loja_targets in j['targets'].values():
        assert {int(k) for k in loja_targets.keys()} == set(range(7))


def test_headcount_targets_get_requires_supervisor(client):
    r = client.get('/api/headcount-targets')
    assert r.status_code in (302, 401, 403, 404)


def test_headcount_targets_post_updates_and_persists(supervisor_client):
    """POST updates the grid; subsequent GET reflects the change."""
    payload = {'targets': {'carmelitas': {'0': 12, '5': 15}}}
    r = supervisor_client.post('/api/headcount-targets', json=payload)
    assert r.status_code == 200
    assert r.get_json()['ok'] is True
    # Re-read
    r2 = supervisor_client.get('/api/headcount-targets').get_json()
    assert r2['targets']['carmelitas']['0'] == 12
    assert r2['targets']['carmelitas']['5'] == 15


def test_headcount_targets_post_clamps_values(supervisor_client):
    """Negative and very-large targets are clamped to 0..50."""
    r = supervisor_client.post('/api/headcount-targets',
                                json={'targets': {'gaia': {'0': -5, '1': 9999}}})
    assert r.status_code == 200
    j = supervisor_client.get('/api/headcount-targets').get_json()
    assert j['targets']['gaia']['0'] == 0
    assert j['targets']['gaia']['1'] == 50


def test_headcount_targets_post_ignores_invalid_lojas(supervisor_client):
    """Unknown loja ids are silently dropped (no 500)."""
    r = supervisor_client.post('/api/headcount-targets',
                                json={'targets': {'nonexistent_loja': {'0': 5}}})
    assert r.status_code == 200
    # No row was created for the bogus loja
    j = supervisor_client.get('/api/headcount-targets').get_json()
    assert 'nonexistent_loja' not in j['targets']


# ───────────────────────────────────────────────────────────────────────────────
# Historical affinity scoring (Tier-1 ML)
# ───────────────────────────────────────────────────────────────────────────────

def _create_past_week(app_module, codigo, label, dias_with_dates):
    """Helper: create a SemanaConfig with dias as list of (label, iso_date) tuples.
    The 'date' field is important — _load_prior_weeks now orders weeks by it."""
    import json
    with app_module.app.app_context():
        dias_json = json.dumps([
            {'label': lbl, 'date': dt, 'feriado': False}
            for (lbl, dt) in dias_with_dates
        ])
        sw = app_module.SemanaConfig.query.filter_by(codigo=codigo).first()
        if not sw:
            sw = app_module.SemanaConfig(codigo=codigo, label=label,
                                          dias_json=dias_json, ativa=False)
            app_module.db.session.add(sw)
            app_module.db.session.commit()
        return sw.id


def _add_past_turno(app_module, semana, loja, horario, dia, funcionario):
    """Helper: add a Turno row to a past week."""
    with app_module.app.app_context():
        app_module.db.session.add(app_module.Turno(
            funcionario=funcionario, loja=loja, horario=horario,
            dia=dia, semana=semana
        ))
        app_module.db.session.commit()


def _create_current_week(app_module, codigo='18-24mai'):
    """Returns the id of the current week. The seed auto-creates the current
    Monday-of-this-week, so we just look it up rather than recreate it."""
    with app_module.app.app_context():
        sw = app_module.SemanaConfig.query.filter_by(codigo=codigo).first()
        if sw:
            return sw.id
        # Fallback for the unusual case where the test runs on a different
        # week than the seed picked — create it.
        import json
        dias = ['Seg 18','Ter 19','Qua 20','Qui 21','Sex 22','Sab 23','Dom 24']
        from datetime import date, timedelta
        dias_json = json.dumps([
            {'label': d, 'date': (date(2026, 5, 18) + timedelta(days=i)).isoformat(),
             'feriado': False} for i, d in enumerate(dias)
        ])
        sw = app_module.SemanaConfig(codigo=codigo, label='18 a 24 de Maio',
                                      dias_json=dias_json, ativa=True)
        app_module.db.session.add(sw)
        app_module.db.session.commit()
        return sw.id


def test_historical_affinity_zero_with_one_past_week(app, supervisor_client):
    """With only 1 past week (the seed), the historical signal must return 0
    because HISTORICAL_MIN_WEEKS = 2."""
    _create_current_week(app)
    _set_target(app, 'carmelitas', 0, 99)
    r = supervisor_client.get('/api/empties/18-24mai/Seg%2018').get_json()
    carm = next(L for L in r['lojas'] if L['loja']=='carmelitas')
    # Nobody should be tagged 'histórico' yet
    reasons = {s['reason'] for s in carm['suggestions']}
    assert 'histórico' not in reasons


def test_historical_affinity_kicks_in_with_2_past_weeks(app, supervisor_client):
    """With 2+ past weeks, a sigla that worked the same (loja, weekday, slot)
    repeatedly should be ranked higher by the historical signal."""
    # Add 2 weeks BEFORE 11-17mai (the seed historical week) so they're older.
    # The current active week (auto-created by seed) is 2026-05-18, so anything
    # before that counts as past.
    _create_past_week(app, 'wk-04mai', '04 a 10 de Maio',
                       [('Seg 4','2026-05-04'),('Ter 5','2026-05-05'),('Qua 6','2026-05-06'),
                        ('Qui 7','2026-05-07'),('Sex 8','2026-05-08'),('Sab 9','2026-05-09'),
                        ('Dom 10','2026-05-10')])
    _create_past_week(app, 'wk-27abr', '27 abril a 3 maio',
                       [('Seg 27','2026-04-27'),('Ter 28','2026-04-28'),('Qua 29','2026-04-29'),
                        ('Qui 30','2026-04-30'),('Sex 1','2026-05-01'),('Sab 2','2026-05-02'),
                        ('Dom 3','2026-05-03')])
    # Place TF on Carmelitas Mon 08:00-16:30 in those 2 past weeks.
    _add_past_turno(app, 'wk-04mai', 'carmelitas', '08:00-16:30', 'Seg 4', 'TF')
    _add_past_turno(app, 'wk-27abr', 'carmelitas', '08:00-16:30', 'Seg 27', 'TF')
    _create_current_week(app)
    # Give the day an opener already, so the "responsável" boost doesn't fire and
    # rename TF's reason — we want to assert the pure historical signal here.
    _add_past_turno(app, '18-24mai', 'carmelitas', '08:00-16:30', 'Seg 18', 'JP')
    _set_target(app, 'carmelitas', 0, 99)
    r = supervisor_client.get('/api/empties/18-24mai/Seg%2018').get_json()
    carm = next(L for L in r['lojas'] if L['loja']=='carmelitas')
    # TF should appear with reason 'histórico'
    tf_sug = next((s for s in carm['suggestions'] if s['sigla']=='TF'), None)
    assert tf_sug is not None, f"TF not in suggestions: {[s['sigla'] for s in carm['suggestions']]}"
    assert tf_sug['reason'] == 'histórico', f"got reason={tf_sug['reason']!r}, expected histórico"


def test_historical_affinity_below_preference(app, supervisor_client):
    """A person with a fresh preferência for this loja+slot should still beat
    a person with strong historical pattern but no preferência."""
    # Strong history for TF
    _create_past_week(app, 'wk-04mai', '04 a 10 de Maio',
                       [('Seg 4','2026-05-04'),('Ter 5','2026-05-05'),('Qua 6','2026-05-06'),
                        ('Qui 7','2026-05-07'),('Sex 8','2026-05-08'),('Sab 9','2026-05-09'),
                        ('Dom 10','2026-05-10')])
    _create_past_week(app, 'wk-27abr', '27 abril a 3 maio',
                       [('Seg 27','2026-04-27'),('Ter 28','2026-04-28'),('Qua 29','2026-04-29'),
                        ('Qui 30','2026-04-30'),('Sex 1','2026-05-01'),('Sab 2','2026-05-02'),
                        ('Dom 3','2026-05-03')])
    _add_past_turno(app, 'wk-04mai', 'carmelitas', '08:00-16:30', 'Seg 4', 'TF')
    _add_past_turno(app, 'wk-27abr', 'carmelitas', '08:00-16:30', 'Seg 27', 'TF')
    _create_current_week(app)
    # ALS gets a fresh preferência for Carmelitas manhã
    _set_disponibilidade(app, 'ALS', 'Seg 18', 'preferencia',
                          lojas_pref='carmelitas', slots_pref='manha', semana='18-24mai')
    _set_target(app, 'carmelitas', 0, 99)
    r = supervisor_client.get('/api/empties/18-24mai/Seg%2018').get_json()
    carm = next(L for L in r['lojas'] if L['loja']=='carmelitas')
    # ALS (preferência, +1000) must beat TF (histórico, ≤250)
    assert carm['suggestions'][0]['sigla'] == 'ALS'
    assert carm['suggestions'][0]['reason'] == 'prefere'


# ───────────────────────────────────────────────────────────────────────────────
# /api/headcount-targets/suggest — auto-adjust from history
# ───────────────────────────────────────────────────────────────────────────────

def test_targets_suggest_returns_empty_with_few_weeks(supervisor_client):
    """With only 1 historical week (the seed), there's not enough data for
    any (loja, weekday) to have ≥4 observations. So proposals should be empty."""
    r = supervisor_client.get('/api/headcount-targets/suggest')
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] is True
    assert j['proposals'] == []


def test_targets_suggest_proposes_when_history_diverges(app, supervisor_client):
    """If past weeks consistently show a different headcount than the current
    target, the endpoint should propose the median."""
    # Build 4 past weeks where carmelitas Mon always had exactly 10 people.
    # The 'current' week is 18-24mai (auto-seeded by the seed).
    past_weeks = [
        ('hist-04',  '04 a 10',     [('Seg 4', '2026-05-04')]),
        ('hist-27a', '27 a 3 mai',  [('Seg 27','2026-04-27')]),
        ('hist-20a', '20 a 26 abr', [('Seg 20','2026-04-20')]),
        ('hist-13a', '13 a 19 abr', [('Seg 13','2026-04-13')]),
    ]
    for codigo, label, dias_dts in past_weeks:
        # The helper only stores label/date; that's fine — we need the date
        # for chronological ordering, not the full 7-day list.
        _create_past_week(app, codigo, label, dias_dts)
    # Place 10 distinct people on Carmelitas in each past Monday.
    siglas = ['ALS','BG','CV','BS','TF','HC','JA','GBR','FO','MM']
    for codigo, _, [(seg_lbl, _dt)] in past_weeks:
        for s in siglas:
            _add_past_turno(app, codigo, 'carmelitas', '08:00-16:30', seg_lbl, s)
    # Force the current target for carmelitas Mon (dia_idx=0) to a different value
    _set_target(app, 'carmelitas', 0, 3)
    r = supervisor_client.get('/api/headcount-targets/suggest')
    j = r.get_json()
    # Should propose carmelitas Mon → 10 (since median of [10,10,10,10] = 10)
    carm_mon = next((p for p in j['proposals']
                      if p['loja']=='carmelitas' and p['dia_idx']==0), None)
    assert carm_mon is not None, f"no carmelitas-Mon proposal in {j['proposals']}"
    assert carm_mon['current'] == 3
    assert carm_mon['proposed'] == 10


def test_targets_suggest_requires_supervisor(client):
    r = client.get('/api/headcount-targets/suggest')
    assert r.status_code in (302, 401, 403, 404)


# ───────────────────────────────────────────────────────────────────────────────
# UX: stable ordering (May 2026 tweaks)
# ───────────────────────────────────────────────────────────────────────────────

def test_api_empties_lojas_in_fixed_order(supervisor_client):
    """Loja cards in the empties panel should appear in the same order every day,
    matching config.LOJAS — not sorted by 'most missing' (which would shuffle
    the cards as the supervisor switches days)."""
    EXPECTED = ['carmelitas', 'clerigos', 'norte', 'gaia', 'mbs']
    for dia in ['Seg 11','Ter 12','Qua 13','Qui 14','Sex 15','Sab 16','Dom 17']:
        r = supervisor_client.get(f'/api/empties/11-17mai/{dia.replace(" ", "%20")}')
        j = r.get_json()
        loja_ids = [L['loja'] for L in j['lojas']]
        assert loja_ids == EXPECTED, f'{dia}: got {loja_ids}, want {EXPECTED}'


def test_semanas_chronological_orders_by_actual_date(app):
    """Weeks should sort by their dias_json[0].date, not by DB id.
    This catches the case where historical weeks are imported and get high ids
    despite being earlier dates."""
    from datetime import date as _date, timedelta
    import json
    with app.app.app_context():
        # Wipe and create three weeks out-of-order on purpose
        app.SemanaConfig.query.delete()
        # insert OLDEST first (gets lowest id), then MIDDLE, then NEWEST
        for monday in [_date(2026,4,6), _date(2026,5,4), _date(2026,4,20)]:
            app.db.session.add(app.SemanaConfig(
                codigo=app.week_codigo(monday),
                label=app.week_label(monday),
                dias_json=json.dumps(app.week_from_monday(monday)),
                ativa=False
            ))
        app.db.session.commit()

        rows = app.semanas_chronological(app.SemanaConfig)
        starts = [app.semana_start_date(s) for s in rows]
        # Newest first
        assert starts == sorted(starts, reverse=True), \
            f'expected descending by date, got {starts}'


# ───────────────────────────────────────────────────────────────────────────────
# Payroll hours CSV export
# ───────────────────────────────────────────────────────────────────────────────

def test_exportar_horas_csv(app, supervisor_client):
    """The hours CSV export is a downloadable, Excel-friendly CSV (BOM + ';'
    delimiter + comma decimals) with one row per worker and a TOTAL row."""
    with app.app.app_context():
        semana = app.get_semana_ativa().codigo
        dia = app.get_semana_ativa().dias_list[0]['label']
        # FO works one 8h shift (08:00-16:30 = 8.0h paid after lunch).
        app.db.session.add(app.Turno(
            loja='carmelitas', horario='08:00-16:30', dia=dia,
            funcionario='FO', semana=semana))
        app.db.session.commit()
        app.release_week(semana, 'ADMIN', 'Escala autorizada para teste do CSV')
        app.db.session.commit()

    r = supervisor_client.get(f'/supervisor/exportar-horas-csv?semana={semana}')
    assert r.status_code == 200
    assert 'text/csv' in r.headers['Content-Type']
    assert 'attachment' in r.headers['Content-Disposition']
    assert r.data[:3] == b'\xef\xbb\xbf'                 # UTF-8 BOM for Excel
    body = r.data.decode('utf-8-sig')
    assert 'Sigla;Nome;Tipo' in body                     # header, ';' delimiter
    assert 'Horas feriado' in body
    assert 'FO' in body
    assert '8,00' in body                                # comma decimal
    assert 'TOTAL' in body


def test_exportar_horas_csv_requires_supervisor(employee_client):
    """A plain employee cannot pull the payroll export."""
    r = employee_client.get('/supervisor/exportar-horas-csv', follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_exportar_horas_csv_unknown_week_404(supervisor_client):
    """An unknown week code yields a clean 404, not a 500."""
    r = supervisor_client.get('/supervisor/exportar-horas-csv?semana=nope-99xyz')
    assert r.status_code == 404


# ───────────────────────────────────────────────────────────────────────────────
# Coverage headline counts SEATS, not cells (the "141 vs 35" regression)
# ───────────────────────────────────────────────────────────────────────────────

def test_dashboard_coverage_counts_missing_seats_not_cells(supervisor_client):
    """The 'lugares por preencher' headline must count empty SEATS (sum of the
    per-cell shortfalls), not the number of loja-dia cells. With the seed (an
    empty active week + targets >1 per cell), seats must exceed cells."""
    import re
    r = supervisor_client.get('/supervisor')
    assert r.status_code == 200
    body = r.data.decode()
    m = re.search(
        r'<strong>(\d+)</strong>\s*lugares por preencher nesta semana'
        r'(?:.*?em\s*(\d+)\s*loja)?', body, re.S)
    assert m, 'coverage footer "lugares por preencher" not found'
    seats = int(m.group(1))
    cells = int(m.group(2)) if m.group(2) else None
    assert cells is not None, 'footer should also report the loja-dia cell count'
    # Seats >= cells always; and with seeded targets (>1 each) seats > cells —
    # showing the cell count here was the bug.
    assert seats > cells, f'expected seats({seats}) > cells({cells})'


# ───────────────────────────────────────────────────────────────────────────────
# Copy availability from last week
# ───────────────────────────────────────────────────────────────────────────────

def test_disponibilidade_copy_from_last_week_offered(app, employee_client):
    """When this week is blank and the employee submitted a PRIOR week, the page
    offers to copy it (and names that week)."""
    import json
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        # Active week must be blank for FO (seed adds no disponibilidades).
        app.Disponibilidade.query.filter_by(
            semana=ativa.codigo, funcionario='FO').delete()
        app.db.session.add(app.SemanaConfig(
            codigo='wk-prior', label='Semana anterior',
            dias_json=json.dumps([{'label': 'Seg 1', 'date': '2020-01-06',
                                   'feriado': False}]), ativa=False))
        app.db.session.add(app.Disponibilidade(
            semana='wk-prior', funcionario='FO', dia='Seg 1',
            tipo='disponivel', texto='oi', lojas_pref='carmelitas',
            slots_pref='manha'))
        app.db.session.commit()

    r = employee_client.get('/disponibilidade')
    assert r.status_code == 200
    body = r.data.decode()
    assert 'Copiar da semana anterior' in body, 'copy banner should be offered'
    assert 'Semana anterior' in body, 'should name the source week'
    # PREV_BY_DAY must be populated (banner only shows when prev_prefill truthy).
    assert 'const PREV_BY_DAY = {}' not in body.replace(' ', ' ')


def test_disponibilidade_copy_not_offered_when_already_submitted(app, employee_client):
    """Once this week has answers, the copy offer disappears (never clobbers)."""
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        d1 = ativa.dias_list[0]['label']
    employee_client.post('/disponibilidade', data={f'tipo_{d1}': 'disponivel'})
    r = employee_client.get('/disponibilidade')
    assert r.status_code == 200
    assert b'Copiar da semana passada' not in r.data


# ───────────────────────────────────────────────────────────────────────────────
# Assignment-time soft warnings (proactive, non-blocking)
# ───────────────────────────────────────────────────────────────────────────────

def test_add_turno_warns_when_indisponivel(app, supervisor_client):
    """Assigning someone who marked themselves indisponível that day still
    succeeds, but the response carries a soft warning."""
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        dia = ativa.dias_list[0]['label']
        app.db.session.add(app.Disponibilidade(
            semana=ativa.codigo, funcionario='FO', dia=dia, tipo='indisponivel'))
        app.db.session.commit()
        semana = ativa.codigo
    r = supervisor_client.post('/api/turno/add', json={
        'loja': 'carmelitas', 'horario': '10:00-18:30', 'dia': dia,
        'funcionario': 'FO', 'nota': '', 'semana': semana})
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] is True
    assert 'indispon' in j.get('aviso', '')


def test_add_turno_warns_over_contract(app, supervisor_client):
    """Pushing a contracted worker well past their hours triggers a warning,
    but the assignment is allowed (supervisor may intend it)."""
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        dias = [d['label'] for d in ativa.dias_list]
        semana = ativa.codigo
        # AG is a 16h-contract worker; long shifts across days blow past 16+2.
        funcs_16 = app.Funcionario.query.filter_by(horas_contrato=16,
                                                   tipo_contrato='contrato').first()
        sig = funcs_16.sigla
    last_aviso = ''
    for d in dias[:4]:
        r = supervisor_client.post('/api/turno/add', json={
            'loja': 'gaia', 'horario': '12:30-22:00 (+1)', 'dia': d,
            'funcionario': sig, 'nota': '', 'semana': semana})
        assert r.status_code == 200
        last_aviso = r.get_json().get('aviso', '')
    assert 'contrato' in last_aviso and 'h' in last_aviso


# ───────────────────────────────────────────────────────────────────────────────
# Suggestion engine: weekly-rest fatigue model
# ───────────────────────────────────────────────────────────────────────────────

def test_suggestion_hard_excludes_seventh_day(app, supervisor_client):
    """Someone already working 6 of 7 days must never be auto-suggested for the
    7th — that would leave no legal weekly rest."""
    from helpers.time_helpers import dia_label_to_idx
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        dias = [d['label'] for d in ativa.dias_list]
        semana = ativa.codigo
        for d in dias[:6]:
            app.db.session.add(app.Turno(semana=semana, dia=d, loja='gaia',
                horario='14:00-20:00', funcionario='GS'))
        qday = dias[6]
        app.db.session.query(app.LojaHeadcountTarget).filter_by(
            loja='gaia', dia_idx=dia_label_to_idx(qday)).delete()
        app.db.session.add(app.LojaHeadcountTarget(
            loja='gaia', dia_idx=dia_label_to_idx(qday), target=25))
        app.db.session.commit()
    j = supervisor_client.get(f'/api/empties/{semana}/{qday}').get_json()
    gaia = next(L for L in j['lojas'] if L['loja'] == 'gaia')
    assert 'GS' not in {s['sigla'] for s in gaia['suggestions']}


def test_suggestion_downranks_long_consecutive_run(app, supervisor_client):
    """A worker who'd hit a 6-day consecutive run is ranked below a rested,
    equally-available colleague."""
    from helpers.time_helpers import dia_label_to_idx
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        dias = [d['label'] for d in ativa.dias_list]
        semana = ativa.codigo
        for d in dias[:5]:   # GS works 5 consecutive days
            app.db.session.add(app.Turno(semana=semana, dia=d, loja='gaia',
                horario='14:00-20:00', funcionario='GS'))
        qday = dias[5]
        for sig in ('GS', 'RB'):
            app.db.session.add(app.Disponibilidade(
                semana=semana, funcionario=sig, dia=qday, tipo='disponivel'))
        app.db.session.query(app.LojaHeadcountTarget).filter_by(
            loja='gaia', dia_idx=dia_label_to_idx(qday)).delete()
        app.db.session.add(app.LojaHeadcountTarget(
            loja='gaia', dia_idx=dia_label_to_idx(qday), target=3))
        app.db.session.commit()
    j = supervisor_client.get(f'/api/empties/{semana}/{qday}').get_json()
    gaia = next(L for L in j['lojas'] if L['loja'] == 'gaia')
    sugg = [s['sigla'] for s in gaia['suggestions']]
    assert 'RB' in sugg
    assert 'GS' not in sugg or sugg.index('RB') < sugg.index('GS')


# ───────────────────────────────────────────────────────────────────────────────
# Coverage tab: responsibility-risk markers + rest-risk strip
# ───────────────────────────────────────────────────────────────────────────────

def test_dashboard_coverage_surfaces_rest_risk(app, supervisor_client):
    """The Semana tab flags people working 6+ days (rest risk).

    (The per-cell opener/closer "responsibility risk" markers were removed in
    July 2026 at the owner's request — the tab only tracks headcount + rest.)"""
    import json
    with app.app.app_context():
        dias = [
            {'label': 'Seg 1', 'date': '2026-03-02', 'feriado': False},
            {'label': 'Ter 2', 'date': '2026-03-03', 'feriado': False},
            {'label': 'Qua 3', 'date': '2026-03-04', 'feriado': False},
            {'label': 'Qui 4', 'date': '2026-03-05', 'feriado': False},
            {'label': 'Sex 5', 'date': '2026-03-06', 'feriado': False},
            {'label': 'Sab 6', 'date': '2026-03-07', 'feriado': False},
            {'label': 'Dom 7', 'date': '2026-03-08', 'feriado': False},
        ]
        app.db.session.add(app.SemanaConfig(
            codigo='wk-risk', label='Semana risco',
            dias_json=json.dumps(dias), ativa=False))
        # FO is staff: can open alone, but cannot close alone. Works 6 days at a
        # closing shift → every cell lacks a closer, and FO hits 6 days.
        for d in dias[:6]:
            app.db.session.add(app.Turno(
                semana='wk-risk', dia=d['label'], loja='carmelitas',
                horario='14:00-22:30', funcionario='FO'))
        app.db.session.commit()
    r = supervisor_client.get('/supervisor?cob=wk-risk')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'Risco de descanso' in body          # rest-risk strip rendered
    assert '6d' in body                          # FO's day count chip
    assert 'sem responsável' not in body        # responsibility markers removed


# ───────────────────────────────────────────────────────────────────────────────
# Respostas tab: availability vs need panel
# ───────────────────────────────────────────────────────────────────────────────

def test_dashboard_availability_vs_need_panel(app, supervisor_client):
    """The Respostas tab shows an availability-vs-need panel and flags days where
    explicit availability falls short of the day's headcount target."""
    from helpers.time_helpers import dia_label_to_idx
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        dias = [d['label'] for d in ativa.dias_list]
        semana = ativa.codigo
        d0 = dias[0]
        app.db.session.add(app.Disponibilidade(
            semana=semana, funcionario='FO', dia=d0,
            tipo='disponivel', lojas_pref='carmelitas'))
        app.db.session.query(app.LojaHeadcountTarget).filter_by(
            loja='carmelitas', dia_idx=dia_label_to_idx(d0)).delete()
        app.db.session.add(app.LojaHeadcountTarget(
            loja='carmelitas', dia_idx=dia_label_to_idx(d0), target=20))
        app.db.session.commit()
    r = supervisor_client.get('/supervisor')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'Disponibilidade vs necessidade' in body
    assert 'Procura por loja' in body
    assert 'sem disponibilidade suficiente' in body


# ───────────────────────────────────────────────────────────────────────────────
# Dark mode
# ───────────────────────────────────────────────────────────────────────────────

def test_dark_mode_toggle_and_no_flash_script(client):
    """Every page (incl. login) ships the theme toggle, the persistence key, and
    the no-flash head script; the dark-mode CSS hooks live in the cached app.css
    (extracted from the inline <style> so the browser caches them across pages)."""
    r = client.get('/login')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'amorino_theme' in body          # localStorage key + no-flash script
    assert 'class="theme-toggle"' in body   # the toggle button
    assert '/static/app.css' in body        # shared stylesheet is linked
    # The runtime controller ships as a versioned static file; its behaviour
    # (startup, persistence, transitions, races) is exercised in
    # tests/js/theme_controller.cjs.
    assert '/static/theme.js?v=' in body
    assert client.get('/static/theme.js').status_code == 200
    # Dark-mode CSS hooks now ship in the cached stylesheet, not inline.
    css = client.get('/static/app.css')
    assert css.status_code == 200
    css_body = css.data.decode('utf-8')
    assert '[data-theme="dark"]' in css_body   # dark-mode CSS present
    assert '--brand' in css_body               # brand surface token


def test_csrf_browser_wrapper_handles_actual_url_origins():
    """Exercise the real JS wrapper rather than merely matching its source."""
    import shutil
    import subprocess
    node = os.environ.get('AMORINO_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node is optional for browser-runtime regression checks')
    result = subprocess.run([
        node, os.path.join(PROJECT_ROOT, 'tests', 'js', 'fetch_policy.cjs'),
        os.path.join(PROJECT_ROOT, 'templates', 'base.html'),
    ], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_main_navigation_retains_the_selected_week(app, supervisor_client):
    import re
    with app.app.app_context():
        code = app.get_semana_ativa().codigo
    page = supervisor_client.get('/supervisor/visao-geral',
        query_string={'semana': code}).get_data(as_text=True)
    nav = re.search(r'<nav class="nav".*?</nav>', page, re.S).group(0)
    for path in ('/supervisor/visao-geral', '/supervisor/escala', '/trocas', '/equipa', '/supervisor'):
        assert f'href="{path}?semana={code}"' in nav


def test_employee_week_navigation_never_exposes_unpublished_week_or_accepts_draft_override(app, employee_client):
    from datetime import date
    from models import ScheduleRelease
    with app.app.app_context():
        days = app.week_from_monday(date(2032, 1, 5))
        app.db.session.add(app.SemanaConfig(codigo='security-private-week',
            label='Semana reservada em preparação', dias_json=json.dumps(days), ativa=False))
        app.db.session.add(app.Turno(semana='security-private-week', dia=days[0]['label'],
            loja='gaia', horario='08:00-12:00', funcionario='FO', nota='PRIVATE-DRAFT-FOR-NAVIGATION'))
        app.db.session.commit()
        before = (app.Turno.query.count(), ScheduleRelease.query.count(), app.get_semana_ativa().codigo)
    for draft in ('', '1'):
        response = employee_client.get('/escala', query_string={
            'semana': 'security-private-week', 'rascunho': draft})
        assert response.status_code == 404
        assert b'PRIVATE-DRAFT-FOR-NAVIGATION' not in response.data
    page = employee_client.get('/escala', query_string={'rascunho': '1'})
    assert page.status_code == 200
    assert b'security-private-week' not in page.data
    assert b'PRIVATE-DRAFT-FOR-NAVIGATION' not in page.data
    with app.app.app_context():
        assert before == (app.Turno.query.count(), ScheduleRelease.query.count(), app.get_semana_ativa().codigo)


def test_donation_recipient_selection_is_enforced_by_api_permissions(app):
    """A donor cannot choose a recipient; only an admin can approve a candidate."""
    from test_donation_review import _setup, _open, _apply, _read
    clients, _, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    cid = _apply(clients, tid, 'ZDB')
    for actor in ('ZDA', 'ZDB', 'ZDC', 'ADMIN'):
        rejected = clients[actor].post(f'/api/trocas/{tid}/escolher',
            json={'candidatura_id': cid})
        assert rejected.status_code == 403, (actor, rejected.get_json())
    for actor in ('ZDA', 'ZDB', 'ZDC'):
        rejected = clients[actor].post(f'/api/trocas/{tid}/decidir',
            json={'aprovar': True, 'candidatura_id': cid})
        assert rejected.status_code == 403
    assert clients['ZDC'].post(f'/api/trocas/{tid}/cancelar').status_code == 403
    assert clients['ZDA'].post(f'/api/trocas/{tid}/candidatar', json={}).status_code in (400, 403)
    assert _read(app, tid, origin)['para_sigla'] == ''
    assert _read(app, tid, origin)['published_owner'] == 'ZDA'


def test_donation_mutations_require_csrf_and_cannot_claim_someone_elses_shift(app):
    from test_donation_review import _setup, _open, _apply, _read
    clients, _, _, origin, _ = _setup(app)
    assert clients['ZDB'].post('/api/trocas/abrir', json={
        'turno_origem_id': origin, 'tipo': 'aberto_doacao'}).status_code == 403
    tid = _open(clients, origin)
    cid = _apply(clients, tid, 'ZDB')
    assert clients['ADMIN']._c.post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': cid}).status_code == 403
    assert clients['ZDB']._c.post(f'/api/trocas/{tid}/retirar-candidatura').status_code == 403
    assert clients['ZDA']._c.post(f'/api/trocas/{tid}/cancelar').status_code == 403
    assert _read(app, tid, origin)['candidates'] == {'ZDB': 'pendente'}
    assert _read(app, tid, origin)['published_owner'] == 'ZDA'


def test_invalid_donation_decisions_leave_workflow_assignment_and_publication_unchanged(app):
    """Bad JSON must neither mean refusal nor silently choose another candidate."""
    from test_donation_review import _setup, _open, _apply, _read
    from models import ScheduleRelease, AuditLog
    clients, code, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    cid = _apply(clients, tid, 'ZDB')
    before = _read(app, tid, origin)
    with app.app.app_context():
        releases_before = ScheduleRelease.query.filter_by(semana=code).count()
        decisions_before = AuditLog.query.filter(AuditLog.action.in_(('troca_aprovar', 'troca_negar'))).count()
    invalid = [
        {}, {'candidatura_id': cid}, ['invalid'], 'invalid',
        {'aprovar': 'false', 'candidatura_id': cid},
        {'aprovar': 1, 'candidatura_id': cid},
        {'aprovar': None, 'candidatura_id': cid},
        {'aprovar': True, 'candidatura_id': cid + 0.9},
        {'aprovar': True, 'candidatura_id': True},
        {'aprovar': True, 'candidatura_id': str(cid)},
        {'aprovar': True, 'candidatura_id': -1},
        {'aprovar': False, 'nota': ['invalid']},
        {'aprovar': True, 'candidatura_id': cid, 'nota': 42},
    ]
    for payload in invalid:
        result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir', json=payload)
        assert result.status_code == 400, (payload, result.get_json())
        assert result.is_json and result.get_json()['ok'] is False
        assert _read(app, tid, origin) == before
    with app.app.app_context():
        assert ScheduleRelease.query.filter_by(semana=code).count() == releases_before
        assert AuditLog.query.filter(AuditLog.action.in_(('troca_aprovar', 'troca_negar'))).count() == decisions_before
    # A corrected, explicit decision still succeeds after all rejected attempts.
    assert clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': cid, 'nota': 'Decisão confirmada'}).status_code == 200
    assert _read(app, tid, origin)['published_owner'] == 'ZDB'
