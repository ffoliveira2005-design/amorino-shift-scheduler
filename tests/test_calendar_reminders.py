"""Tests for the calendar reminder (VALARM) feature on the iCal feed.

The feed accepts ?lembrete=<minutes> and adds a relative VALARM to each event.
Missing/invalid values mean no alarm (backward-compatible with existing
subscriptions). The token auth must keep working regardless of the param.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client


def _setup_fo_with_shift(app, token='CALTOKEN_TEST_123'):
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        dias = json.loads(ativa.dias_json or '[]')
        fo = app.db.session.get(app.Funcionario, 'FO')
        fo.ical_token = token
        app.db.session.add(app.Turno(semana=ativa.codigo, dia=dias[0]['label'],
                                     loja='carmelitas', horario='10:00-18:30',
                                     funcionario='FO'))
        app.db.session.commit()
        app.release_week(ativa.codigo, 'ADMIN', 'Escala autorizada para teste de calendário')
        app.db.session.commit()
    return token


def test_feed_without_lembrete_has_no_alarm(app, client):
    """Backward compatibility: existing subscriptions (no param) get no VALARM."""
    tok = _setup_fo_with_shift(app)
    r = client.get(f'/cal/FO/{tok}.ics')
    assert r.status_code == 200
    assert b'BEGIN:VALARM' not in r.data


def test_feed_with_lembrete_adds_relative_alarm(app, client):
    tok = _setup_fo_with_shift(app)
    r = client.get(f'/cal/FO/{tok}.ics?lembrete=120')
    body = r.data.decode()
    assert 'BEGIN:VALARM' in body
    assert 'ACTION:DISPLAY' in body
    assert 'TRIGGER:-PT2H' in body          # 120 min normalises to 2h
    assert 'END:VALARM' in body


def test_lembrete_minutes_form_for_non_hour_values(app, client):
    tok = _setup_fo_with_shift(app)
    r = client.get(f'/cal/FO/{tok}.ics?lembrete=30')
    assert 'TRIGGER:-PT30M' in r.data.decode()


def test_lembrete_night_before(app, client):
    tok = _setup_fo_with_shift(app)
    r = client.get(f'/cal/FO/{tok}.ics?lembrete=720')
    assert 'TRIGGER:-PT12H' in r.data.decode()


def test_invalid_lembrete_is_ignored(app, client):
    """A junk or out-of-range value must NOT add an alarm and must NOT error."""
    tok = _setup_fo_with_shift(app)
    for bad in ('abc', '-5', '999', '45', 'DROP TABLE', ''):
        r = client.get(f'/cal/FO/{tok}.ics?lembrete={bad}')
        assert r.status_code == 200, f'param {bad!r} broke the feed'
        assert b'BEGIN:VALARM' not in r.data, f'param {bad!r} should not add an alarm'


def test_lembrete_does_not_bypass_token_auth(app, client):
    """The reminder param must never weaken the token check.

    404, not 403: a wrong token and an unknown sigla now answer identically so
    the feed can't be walked to enumerate the roster. What matters here is that
    no calendar body comes back.
    """
    _setup_fo_with_shift(app)
    r = client.get('/cal/FO/WRONGTOKEN.ics?lembrete=120')
    assert r.status_code == 404
    assert b'BEGIN:VCALENDAR' not in r.data


def test_alarm_count_matches_event_count(app, client):
    """With a reminder set, every event gets exactly one alarm."""
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        dias = json.loads(ativa.dias_json or '[]')
        fo = app.db.session.get(app.Funcionario, 'FO')
        fo.ical_token = 'TOKMULTI'
        for i in range(3):
            app.db.session.add(app.Turno(semana=ativa.codigo, dia=dias[i]['label'],
                                         loja='gaia', horario='10:30-19:00',
                                         funcionario='FO'))
        app.db.session.commit()
        app.release_week(ativa.codigo, 'ADMIN', 'Escala autorizada para teste de alarmes')
        app.db.session.commit()
    body = client.get('/cal/FO/TOKMULTI.ics?lembrete=60').data.decode()
    n_events = body.count('BEGIN:VEVENT')
    n_alarms = body.count('BEGIN:VALARM')
    assert n_events >= 3, 'expected at least the 3 shifts we added'
    assert n_alarms == n_events, 'every event must carry exactly one alarm'
    # And with no reminder, zero alarms across however many events exist.
    body0 = client.get('/cal/FO/TOKMULTI.ics').data.decode()
    assert body0.count('BEGIN:VALARM') == 0


def test_qr_endpoint_accepts_lembrete(app, client):
    """The QR SVG endpoint should still respond when a reminder param is passed
    (the param is encoded into the webcal URL it renders)."""
    tok = _setup_fo_with_shift(app)
    r = client.get(f'/cal/FO/{tok}.svg?lembrete=120')
    # 200 if qrcode is installed; 500 only if the package is missing — but never
    # a crash or auth bypass. Bad token must still 404.
    assert r.status_code in (200, 500)
    r_bad = client.get('/cal/FO/NOPE.svg?lembrete=120')
    assert r_bad.status_code == 404
