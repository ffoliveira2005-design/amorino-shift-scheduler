"""Controlled provisioning, recovery and signed-session revocation."""
import os
import sys
import sqlite3
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client, employee_client, _boot_app  # noqa: F401


def _redeem(app_mod, token, sigla='FO', pin='k7m2p', raw=None):
    raw = raw or app_mod.app.test_client()
    assert raw.get('/acesso').status_code == 200
    with raw.session_transaction() as sess:
        csrf = sess['_csrf']
    response = raw.post('/acesso', data={
        'csrf_token': csrf, 'sigla': sigla, 'convite': token,
        'pin': pin, 'pin_confirm': pin,
    }, follow_redirects=False)
    return raw, response


def test_sigla_alone_cannot_claim_or_reclaim(app, client, supervisor_client):
    for endpoint in ('/api/funcionario/convite/FO', '/api/funcionario/reset-pin/FO'):
        assert supervisor_client.post(endpoint).status_code == 200
        anonymous = app.app.test_client()
        response = anonymous.post('/login', data={'sigla': 'FO', 'pin': 'k7m2p'})
        assert response.status_code == 200
        assert b'Sigla ou PIN incorreto' in response.data
        with app.app.app_context():
            assert not app.db.session.get(app.Funcionario, 'FO').pin


def test_invitation_stored_as_digest_redeemed_once_and_audited(app, supervisor_client):
    issued = supervisor_client.post('/api/funcionario/convite/FO')
    assert issued.status_code == 200
    assert 'no-store' in issued.headers['Cache-Control']
    token = issued.get_json()['convite']
    assert len(token) >= 40
    with app.app.app_context():
        employee = app.db.session.get(app.Funcionario, 'FO')
        assert employee.enrollment_hash == app.invitation_digest(token)
        assert employee.enrollment_hash != token
    raw, redeemed = _redeem(app, token)
    assert redeemed.status_code == 302
    assert raw.get('/disponibilidade').status_code == 200
    with app.app.app_context():
        employee = app.db.session.get(app.Funcionario, 'FO')
        assert app._looks_hashed(employee.pin)
        assert app.verify_pin(employee.pin, 'k7m2p')
        assert not employee.enrollment_hash
        assert employee.enrollment_expires_at is None
        logs = app.AuditLog.query.filter_by(target='FO').all()
        assert any(row.action == 'enrollment_issued' and row.actor == 'ADMIN' for row in logs)
        assert any(row.action == 'enrollment_complete' for row in logs)
        assert all(token not in row.detail and 'k7m2p' not in row.detail for row in logs)
    _, reused = _redeem(app, token, pin='q9r4s')
    assert reused.status_code == 200
    assert 'Convite inválido ou expirado' in reused.get_data(as_text=True)


def test_expired_invitation_is_rejected(app, supervisor_client):
    token = supervisor_client.post('/api/funcionario/convite/FO').get_json()['convite']
    with app.app.app_context():
        employee = app.db.session.get(app.Funcionario, 'FO')
        employee.enrollment_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        app.db.session.commit()
    _, response = _redeem(app, token)
    assert response.status_code == 200
    assert 'Convite inválido ou expirado' in response.get_data(as_text=True)
    with app.app.app_context():
        assert not app.db.session.get(app.Funcionario, 'FO').pin


def test_reissue_invalidates_previous_invitation(app, supervisor_client):
    old = supervisor_client.post('/api/funcionario/convite/FO').get_json()['convite']
    new = supervisor_client.post('/api/funcionario/convite/FO').get_json()['convite']
    assert old != new
    assert _redeem(app, old)[1].status_code == 200
    assert _redeem(app, new)[1].status_code == 302


def test_invitation_cannot_be_used_for_another_account(app, supervisor_client):
    token = supervisor_client.post('/api/funcionario/convite/FO').get_json()['convite']
    _, response = _redeem(app, token, sigla='GS')
    assert response.status_code == 200
    with app.app.app_context():
        assert not app.db.session.get(app.Funcionario, 'GS').pin
    assert _redeem(app, token)[1].status_code == 302


def test_issue_requires_supervisor_and_csrf(app, employee_client):
    raw = app.app.test_client()
    for endpoint in ('/api/funcionario/convite/FO', '/api/funcionario/reset-pin/FO'):
        assert raw.post(endpoint).status_code == 401
        assert employee_client.post(endpoint).status_code == 403
    raw.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'})
    for endpoint in ('/api/funcionario/convite/FO', '/api/funcionario/reset-pin/FO'):
        assert raw.post(endpoint).status_code == 403
    with app.app.app_context():
        assert app.db.session.get(app.Funcionario, 'FO').access_version == 0


def test_redemption_requires_anonymous_csrf(app, supervisor_client):
    token = supervisor_client.post('/api/funcionario/convite/FO').get_json()['convite']
    raw = app.app.test_client()
    assert raw.post('/acesso', data={'sigla': 'FO', 'convite': token, 'pin': 'k7m2p', 'pin_confirm': 'k7m2p'}).status_code == 403
    assert _redeem(app, token, raw=raw)[1].status_code == 302


def test_reset_invalidates_pin_sessions_and_calendar_immediately(app, supervisor_client):
    with app.app.app_context():
        employee = app.db.session.get(app.Funcionario, 'FO')
        employee.pin = app.hash_pin('old-access')
        employee.ical_token = 'private-calendar-credential'
        app.db.session.commit()
    employees = [app.app.test_client(), app.app.test_client()]
    for raw in employees:
        assert raw.post('/login', data={'sigla': 'FO', 'pin': 'old-access'}).status_code == 302
    issued = supervisor_client.post('/api/funcionario/reset-pin/FO')
    token = issued.get_json()['convite']
    with app.app.app_context():
        employee = app.db.session.get(app.Funcionario, 'FO')
        assert employee.access_version == 1
        assert not employee.pin and not employee.ical_token
    for raw in employees:
        assert raw.get('/disponibilidade', follow_redirects=False).status_code == 302
    assert app.app.test_client().post('/login', data={'sigla': 'FO', 'pin': 'old-access'}).status_code == 200
    assert app.app.test_client().get('/cal/FO/private-calendar-credential.ics').status_code == 404
    assert _redeem(app, token, pin='new-access')[1].status_code == 302
    # Even after a new PIN exists, the previous signed cookies cannot revive.
    for raw in employees:
        assert raw.get('/disponibilidade', follow_redirects=False).status_code == 302


def test_reset_rejects_replay_of_old_signed_cookie_after_recovery(app, supervisor_client):
    with app.app.app_context():
        app.db.session.get(app.Funcionario, 'FO').pin = app.hash_pin('old-access')
        app.db.session.commit()
    raw = app.app.test_client()
    raw.post('/login', data={'sigla': 'FO', 'pin': 'old-access'})
    cookie = raw.get_cookie('session').value
    token = supervisor_client.post('/api/funcionario/reset-pin/FO').get_json()['convite']
    assert _redeem(app, token, pin='new-access')[1].status_code == 302
    raw.set_cookie('session', cookie)
    assert raw.get('/api/cal-link').status_code == 401


def test_existing_valid_hash_and_short_legacy_pin_continue_working(app):
    for stored in (app.hash_pin('existing-access'), 'xy'):
        with app.app.app_context():
            app.db.session.get(app.Funcionario, 'FO').pin = stored
            app.db.session.commit()
        supplied = 'existing-access' if app._looks_hashed(stored) else 'xy'
        assert app.app.test_client().post('/login', data={'sigla': 'FO', 'pin': supplied}).status_code == 302


def test_invite_cannot_overwrite_an_active_account(app, supervisor_client):
    with app.app.app_context():
        app.db.session.get(app.Funcionario, 'FO').pin = app.hash_pin('existing-access')
        app.db.session.commit()
    assert supervisor_client.post('/api/funcionario/convite/FO').status_code == 409
    assert app.app.test_client().post('/login', data={'sigla': 'FO', 'pin': 'existing-access'}).status_code == 302


def test_weak_new_pin_does_not_consume_valid_invitation(app, supervisor_client):
    token = supervisor_client.post('/api/funcionario/convite/FO').get_json()['convite']
    for pin in ('12', '1234', 'aaaa', 'FO', 'x' * 33):
        assert _redeem(app, token, pin=pin)[1].status_code == 200
    assert _redeem(app, token)[1].status_code == 302


def test_failed_redemptions_use_existing_login_rate_limit(app, supervisor_client):
    token = supervisor_client.post('/api/funcionario/convite/FO').get_json()['convite']
    for _ in range(app.LOGIN_MAX):
        assert _redeem(app, 'wrong-secret')[1].status_code == 200
    _, response = _redeem(app, token)
    assert response.status_code == 200
    assert b'Demasiadas tentativas' in response.data


def test_existing_schema_migration_preserves_valid_and_unclaimed_accounts(app, tmp_path):
    """Rehearse the real startup upgrade using only a copied scratch database."""
    with app.app.app_context():
        app.db.session.get(app.Funcionario, 'FO').pin = app.hash_pin('existing-access')
        app.db.session.commit()
    scratch_path = os.environ['AMORINO_DB_PATH']
    migrated = tmp_path / 'legacy-access.db'
    with sqlite3.connect(scratch_path) as source, sqlite3.connect(migrated) as legacy:
        source.backup(legacy)
        before_roster = legacy.execute('SELECT sigla, pin FROM funcionarios ORDER BY sigla').fetchall()
        before_availability = legacy.execute('SELECT COUNT(*) FROM disponibilidades').fetchone()[0]
        before_shifts = legacy.execute('SELECT COUNT(*) FROM turnos').fetchone()[0]
        for column in ('access_version', 'enrollment_hash', 'enrollment_expires_at', 'enrollment_kind'):
            legacy.execute(f'ALTER TABLE funcionarios DROP COLUMN {column}')
        legacy.commit()
    rc, output = _boot_app({'AMORINO_DB_PATH': str(migrated)})
    assert rc == 0, output[-1200:]
    with sqlite3.connect(migrated) as upgraded:
        assert upgraded.execute('SELECT sigla, pin FROM funcionarios ORDER BY sigla').fetchall() == before_roster
        assert upgraded.execute('SELECT COUNT(*) FROM disponibilidades').fetchone()[0] == before_availability
        assert upgraded.execute('SELECT COUNT(*) FROM turnos').fetchone()[0] == before_shifts
        assert upgraded.execute('SELECT DISTINCT access_version, enrollment_hash, enrollment_expires_at, enrollment_kind FROM funcionarios').fetchall() == [(0, '', None, '')]
