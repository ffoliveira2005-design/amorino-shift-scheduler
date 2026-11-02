"""Tests for the August 2026 design + security review.

Covers:
  - Stored XSS: free-text fields (nome, horario, availability note) must never
    be concatenated into innerHTML. The live ones are `nome` and `horario` —
    both typed in the supervisor panel and rendered in every employee's
    browser, so a compromised supervisor session reaches the whole team.
  - The availability note is capped server-side to its column width.
  - The iCal feed answers unknown-sigla and wrong-token identically, so it
    can't be walked to enumerate the staff roster.
  - safe_referrer() rejects the '/\\' protocol-relative trick.
  - @login_required answers /api/* with 401 JSON instead of redirecting to the
    login PAGE, which fetch() follows and every call site fails to parse.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import (PROJECT_ROOT, app, client, supervisor_client,  # noqa: F401
                         employee_client)


def _template(name):
    with open(os.path.join(PROJECT_ROOT, 'templates', name), encoding='utf-8') as fh:
        return fh.read()


# ───────────────────────────────────────────────────────────────────────────────
# Stored XSS — free text into innerHTML
# ───────────────────────────────────────────────────────────────────────────────

def test_base_provides_an_escape_helper():
    """Every innerHTML interpolation below leans on this one helper."""
    src = _template('base.html')
    assert 'function escHtml(' in src, 'base.html no longer defines escHtml()'
    for entity in ('&amp;', '&lt;', '&gt;', '&quot;', '&#39;'):
        assert entity in src, f'escHtml() does not encode {entity}'


def test_availability_note_is_escaped_in_the_assign_panel():
    """The availability note is the only free-text field an EMPLOYEE controls,
    and buildCard() renders it in the supervisor's assign panel — the session
    that can reset PINs and delete people.

    buildCard() currently sits inside _renderPanel_legacy_unused(), which
    nothing calls, so this is not a live hole today. It is guarded anyway
    because the function is explicitly kept "for reference" and reviving it
    would silently reintroduce an employee → supervisor escalation.
    """
    src = _template('escala.html')
    assert "+availText+" not in src, \
        'escala.html builds the availability note into innerHTML unescaped'
    assert 'escHtml(availText)' in src, \
        'escala.html no longer escapes the availability note'


def test_employee_name_is_escaped_in_the_assign_panel():
    src = _template('escala.html')
    assert "+(e.nome||'')+" not in src, \
        'escala.html builds the employee name into innerHTML unescaped'
    assert "escHtml(e.nome||'')" in src


def test_horario_is_escaped_where_employees_read_it():
    """`horario` is free text typed in the supervisor panel and rendered in
    every employee's swap pickers — escape it there too.

    The pickers live in trocas.html (proposing a swap on the central) and in
    the shared _troca_pedido.html dialog that both /trocas and /minha-escala
    include; colleague names (`nome`) are listed there too."""
    for name in ('trocas.html', '_troca_pedido.html'):
        src = _template(name)
        assert '${t.horario}' not in src, \
            f'{name} builds a shift horario into innerHTML unescaped'
        assert '${escHtml(t.horario)}' in src, \
            f'{name} no longer escapes the shift horario'
    dialog = _template('_troca_pedido.html')
    assert '${c.nome}' not in dialog and '${escHtml(c.nome)}' in dialog
    assert '{% include "_troca_pedido.html" %}' in _template('minha_escala.html')


def test_supervisor_dashboard_escapes_name_and_horario():
    src = _template('supervisor_dashboard.html')
    assert "+ w.nome +" not in src, 'dashboard builds a name into innerHTML unescaped'
    assert "+ w.hor +" not in src, 'dashboard builds a horario into innerHTML unescaped'
    assert 'escHtml(w.nome)' in src and 'escHtml(w.hor)' in src


def test_api_error_text_is_escaped_before_innerhtml():
    """Server error strings are constants today, but they land in innerHTML —
    escape them so that stays true if one ever interpolates user input."""
    for name in ('trocas.html', 'minha_escala.html'):
        src = _template(name)
        assert '${data.erro}' not in src, f'{name} injects data.erro raw'


# ───────────────────────────────────────────────────────────────────────────────
# Input validation
# ───────────────────────────────────────────────────────────────────────────────

def test_availability_note_is_capped_to_the_column_width(app, employee_client):
    """SQLite does not enforce String(200); without an explicit cap an employee
    could store a note the size of the whole 256 KB body cap."""
    with app.app.app_context():
        dia = app.get_semana_ativa().day_labels[0]

    r = employee_client.post('/disponibilidade', data={
        f'tipo_{dia}': 'disponivel',
        f'texto_{dia}': 'A' * 5000,
    })
    assert r.status_code == 200

    with app.app.app_context():
        row = app.Disponibilidade.query.filter_by(
            funcionario='FO', dia=dia).first()
        assert row is not None
        assert len(row.texto) == 200, f'note stored at {len(row.texto)} chars'


# ───────────────────────────────────────────────────────────────────────────────
# iCal feed — no roster enumeration
# ───────────────────────────────────────────────────────────────────────────────

def _cal_url(employee_client):
    r = employee_client.get('/api/cal-link')
    assert r.status_code == 200
    return r.get_json()['url']


def test_ical_feed_serves_the_owner(app, employee_client):
    """The fix must not break the happy path."""
    url = _cal_url(employee_client)
    path = url.split('://', 1)[-1].split('/', 1)[-1]
    r = app.app.test_client().get('/' + path)
    assert r.status_code == 200
    assert b'BEGIN:VCALENDAR' in r.data


def test_ical_feed_does_not_reveal_which_siglas_exist(app, employee_client):
    """SECURITY: 404 for an unknown sigla but 403 for a wrong token told an
    unauthenticated caller exactly which siglas are real staff — the same
    roster enumeration /login was hardened against."""
    _cal_url(employee_client)          # mint FO's token
    raw = app.app.test_client()

    bad_token = raw.get('/cal/FO/' + ('z' * 43) + '.ics')
    no_sigla  = raw.get('/cal/NOSUCH/' + ('z' * 43) + '.ics')

    assert bad_token.status_code == no_sigla.status_code == 404, (
        f'wrong token → {bad_token.status_code}, '
        f'unknown sigla → {no_sigla.status_code}: the pair leaks the roster')
    assert bad_token.data == no_sigla.data, 'response bodies differ'


def test_ical_qr_also_refuses_a_bad_token(app, employee_client):
    _cal_url(employee_client)
    raw = app.app.test_client()
    assert raw.get('/cal/FO/' + ('z' * 43) + '.svg').status_code == 404


# ───────────────────────────────────────────────────────────────────────────────
# Open redirect
# ───────────────────────────────────────────────────────────────────────────────

def test_safe_referrer_rejects_backslash_paths(app):
    """Browsers normalise '/\\evil.com' to '//evil.com'. _is_safe_next() always
    rejected backslashes; safe_referrer() did not, though SECURITY.md described
    both the same way."""
    with app.app.test_request_context('/', headers={'Referer': '/\\evil.com/x'}):
        assert app.safe_referrer('login') != '/\\evil.com/x'
    with app.app.test_request_context('/', headers={'Referer': '//evil.com/x'}):
        assert app.safe_referrer('login') != '//evil.com/x'
    # A genuine same-site path still round-trips.
    with app.app.test_request_context('/', headers={'Referer': '/equipa'}):
        assert app.safe_referrer('login') == '/equipa'


# ───────────────────────────────────────────────────────────────────────────────
# Expired session — API answers must be parseable
# ───────────────────────────────────────────────────────────────────────────────

def test_expired_session_gets_401_json_not_the_login_page(app):
    """A redirect is answered with the login PAGE; fetch() follows it, and every
    call site's `await res.json()` throws on the HTML — the button just does
    nothing. 401 JSON gives the client something to act on."""
    raw = app.app.test_client()
    r = raw.get('/api/trocas/contagem')
    assert r.status_code == 401, f'expected 401, got {r.status_code}'
    assert r.mimetype == 'application/json', 'API answered with a non-JSON body'
    body = r.get_json()
    assert body['ok'] is False
    assert body['sessao_expirada'] is True


def test_deleted_funcionario_gets_401_on_api_not_a_redirect(app, employee_client):
    with app.app.app_context():
        app.db.session.delete(app.db.session.get(app.Funcionario, 'FO'))
        app.db.session.commit()
    r = employee_client.get('/api/trocas/contagem')
    assert r.status_code == 401
    assert r.get_json()['sessao_expirada'] is True


def test_html_pages_still_redirect_to_login(app):
    """Only /api/* changes. A browser hitting a page must still get sent to the
    login form, with `next` so it lands back where it was."""
    raw = app.app.test_client()
    r = raw.get('/minha-escala', follow_redirects=False)
    assert r.status_code == 302
    assert '/login' in r.headers['Location']
    assert 'next=' in r.headers['Location']


def test_base_html_surfaces_the_expired_session(app):
    """The 401 is useless if no call site acts on it — base.html's fetch wrapper
    handles it once, centrally, rather than at 40-odd call sites."""
    src = _template('base.html')
    assert 'res.status === 401' in src, \
        'base.html fetch wrapper no longer detects an expired session'
    assert 'A sessão expirou' in src
