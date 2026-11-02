"""One-process publication consistency under concurrency and transaction failure.

Every fixture uses AMORINO_DB_PATH and a disposable database. Event gates pause
real request rendering/serialization; publication must remain blocked until the
complete old-version response is formed. No sleep-based race assumptions.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import os
import sys
import threading

import pytest
from flask import request, session
from sqlalchemy import event

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client, _CsrfClient  # noqa: F401
from test_publication import setup_week, login, publish
from helpers.publication import schedule_lock
from models import PublishedTurno, PublishedDescarga, PublishedSemana, ScheduleRelease


def _supervisor(mod):
    client = _CsrfClient(mod.app.test_client(), mod)
    assert client.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'}).status_code == 302
    return client


def _stage(mod, supervisor, code, days, tid):
    assert supervisor.post(f'/api/turno/move/{tid}', json={
        'to_dia': days[0]['label'], 'to_loja': 'gaia',
        'to_horario': '12:00-16:00', 'nota': 'PRIVATE-AMENDMENT-AFTER-PAUSE',
    }).status_code == 200
    info = supervisor.get(f'/api/publicacao/{code}').get_json()
    return {'confirmar': True, 'versao_esperada': info['versao'],
            'rascunho_hash': info['rascunho_hash'], 'motivo': 'Alteração revista e comunicada'}


def _announce_publication_attempt(mod, monkeypatch, announced):
    # Signal immediately before the real lock acquisition; preserve all hooks.
    hooks = mod.app.before_request_funcs[None]
    wrapped = []
    for hook in hooks:
        if hook.__name__ == 'capture_schedule_before':
            def announce(hook=hook):
                if request.endpoint == 'api_publicacao_publish':
                    announced.set()
                return hook()
            wrapped.append(announce)
        else:
            wrapped.append(hook)
    monkeypatch.setitem(mod.app.before_request_funcs, None, wrapped)


def _assert_lock_held_by_other_thread():
    acquired = schedule_lock.acquire(blocking=False)
    if acquired:
        schedule_lock.release()
    assert not acquired, 'The employee/export response released its schedule lock too early'


def _request_during_publication(mod, monkeypatch, paused_request, supervisor, code, payload,
                                reached, resume):
    attempted = threading.Event()
    _announce_publication_attempt(mod, monkeypatch, attempted)
    with ThreadPoolExecutor(max_workers=2) as pool:
        read = pool.submit(paused_request)
        try:
            assert reached.wait(5), 'The real request did not reach the pause gate'
            _assert_lock_held_by_other_thread()
            publication = pool.submit(supervisor.post, f'/api/publicacao/{code}', json=payload)
            assert attempted.wait(5), 'The competing publication did not start'
            with pytest.raises(TimeoutError):
                publication.result(timeout=0.1)
        finally:
            resume.set()
        response = read.result(timeout=5)
        published = publication.result(timeout=5)
        assert published.status_code == 200 and published.get_json()['versao'] == 2
        # A later request must be able to acquire the lock: teardown released it.
        assert supervisor.get(f'/api/publicacao/{code}').get_json()['versao'] == 2
        return response


def test_employee_page_body_and_rendered_version_are_consistent_during_publish(app, supervisor_client, monkeypatch):
    code, days, tid, _ = setup_week(app)
    employee = login(app)
    assert publish(supervisor_client, code).get_json()['versao'] == 1
    payload = _stage(app, supervisor_client, code, days, tid)
    reached, resume = threading.Event(), threading.Event()
    render = app.render_template

    def paused_render(*args, **kwargs):
        if request.endpoint == 'minha_escala' and not session.get('supervisor'):
            reached.set()
            assert resume.wait(5)
        return render(*args, **kwargs)

    monkeypatch.setattr(app, 'render_template', paused_render)
    response = _request_during_publication(app, monkeypatch,
        lambda: employee.get('/minha-escala', query_string={'semana': code}),
        supervisor_client, code, payload, reached, resume)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'SECRET-DRAFT-ORIGINAL' in body and 'PRIVATE-AMENDMENT-AFTER-PAUSE' not in body
    assert 'Escala publicada · versão 1' in body
    subsequent = employee.get('/minha-escala', query_string={'semana': code}).get_data(as_text=True)
    assert 'PRIVATE-AMENDMENT-AFTER-PAUSE' in subsequent
    assert 'Escala publicada · versão 2' in subsequent


def test_export_body_and_after_request_version_header_are_consistent(app, supervisor_client, monkeypatch):
    code, days, tid, _ = setup_week(app)
    assert publish(supervisor_client, code).get_json()['versao'] == 1
    exporter = _supervisor(app)
    original = exporter.get('/supervisor/exportar-horas-csv', query_string={'semana': code})
    payload = _stage(app, supervisor_client, code, days, tid)
    reached, resume = threading.Event(), threading.Event()
    hooks = []
    for hook in app.app.after_request_funcs[None]:
        if hook.__name__ == 'mark_schedule_exports':
            def paused_metadata(response, hook=hook):
                if request.endpoint == 'exportar_horas_csv':
                    reached.set()
                    assert resume.wait(5)
                return hook(response)
            hooks.append(paused_metadata)
        else:
            hooks.append(hook)
    monkeypatch.setitem(app.app.after_request_funcs, None, hooks)
    response = _request_during_publication(app, monkeypatch,
        lambda: exporter.get('/supervisor/exportar-horas-csv', query_string={'semana': code}),
        supervisor_client, code, payload, reached, resume)
    assert response.status_code == 200 and response.data == original.data
    assert response.headers['X-Amorino-Schedule-Version'] == 'v1'
    assert '-v1.csv' in response.headers['Content-Disposition']
    subsequent = exporter.get('/supervisor/exportar-horas-csv', query_string={'semana': code})
    assert subsequent.data != original.data
    assert subsequent.headers['X-Amorino-Schedule-Version'] == 'v2'


def test_employee_state_changing_swap_holds_published_read_lock_through_response(app, supervisor_client, monkeypatch):
    code, days, tid, _ = setup_week(app)
    with app.app.app_context():
        # This read-lock test concerns a genuine exchange. A donation of an
        # already modified draft shift now correctly fails before serialization.
        offered = app.Turno(semana=code, dia=days[2]['label'], loja='gaia',
                            horario='10:00-14:00', funcionario='ZBB')
        app.db.session.add(offered)
        app.db.session.commit()
        offered_id = offered.id
    employee = login(app)
    assert publish(supervisor_client, code).get_json()['versao'] == 1
    payload = _stage(app, supervisor_client, code, days, tid)
    reached, resume = threading.Event(), threading.Event()
    serialize = app._serialize_troca

    def paused_serialization(*args, **kwargs):
        if request.endpoint == 'api_troca_criar':
            reached.set()
            assert resume.wait(5)
        return serialize(*args, **kwargs)

    monkeypatch.setattr(app, '_serialize_troca', paused_serialization)
    response = _request_during_publication(app, monkeypatch,
        lambda: employee.post('/api/trocas', json={'turno_origem_id': tid, 'para_sigla': 'ZBB',
                                                  'turno_destino_id': offered_id}),
        supervisor_client, code, payload, reached, resume)
    assert response.status_code == 200
    turno = response.get_json()['troca']['turno_origem']
    assert turno['horario'] == '16:00-00:30 (+1)'
    assert '12:00-16:00' not in str(turno)


def test_simultaneous_supervisor_publications_accept_only_one_reviewed_revision(app, supervisor_client):
    code, days, tid, _ = setup_week(app)
    assert publish(supervisor_client, code).get_json()['versao'] == 1
    payload = _stage(app, supervisor_client, code, days, tid)
    competing = _supervisor(app)
    barrier = threading.Barrier(3)

    def publish_reviewed(supervisor):
        barrier.wait(timeout=5)
        return supervisor.post(f'/api/publicacao/{code}', json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(publish_reviewed, supervisor_client)
        second = pool.submit(publish_reviewed, competing)
        barrier.wait(timeout=5)
        responses = [first.result(timeout=5), second.result(timeout=5)]
    assert sorted(response.status_code for response in responses) == [200, 409]
    with app.app.app_context():
        assert [r.version for r in ScheduleRelease.query.filter_by(semana=code).order_by(ScheduleRelease.version)] == [1, 2]
        assert app.db.session.get(PublishedTurno, tid).version == 2


def test_failed_projection_insert_rolls_back_version_history_and_all_projections(app, supervisor_client):
    code, days, tid, did = setup_week(app)
    assert publish(supervisor_client, code).get_json()['versao'] == 1
    payload = _stage(app, supervisor_client, code, days, tid)
    with app.app.app_context():
        engine = app.db.engine
        old_release = app.latest_release(code).snapshot_json
    statements = []

    def fail_projection(connection, cursor, statement, parameters, context, executemany):
        normalized = statement.lower().strip()
        statements.append(normalized)
        if normalized.startswith('insert into published_turnos'):
            raise RuntimeError('Simulated publication projection storage failure')

    event.listen(engine, 'before_cursor_execute', fail_projection)
    try:
        with pytest.raises(RuntimeError, match='projection storage failure'):
            supervisor_client.post(f'/api/publicacao/{code}', json=payload)
    finally:
        event.remove(engine, 'before_cursor_execute', fail_projection)
    # The transaction had already removed projections and inserted history.
    assert any(s.startswith('delete from published_turnos') for s in statements)
    assert any(s.startswith('insert into schedule_releases') for s in statements)
    with app.app.app_context():
        assert ScheduleRelease.query.filter_by(semana=code).count() == 1
        assert app.latest_release(code).snapshot_json == old_release
        shift = app.db.session.get(PublishedTurno, tid)
        assert shift.version == 1 and shift.nota == 'SECRET-DRAFT-ORIGINAL'
        assert shift.horario == '16:00-00:30 (+1)'
        assert app.db.session.get(PublishedDescarga, did).version == 1
        assert app.db.session.get(PublishedSemana, code).version == 1
        assert app.db.session.get(app.Turno, tid).nota == 'PRIVATE-AMENDMENT-AFTER-PAUSE'
    # Teardown also releases the failed request's lock; the reviewed amendment
    # can be retried and creates exactly version 2.
    assert supervisor_client.post(f'/api/publicacao/{code}', json=payload).get_json()['versao'] == 2


def test_public_calendar_stays_published_even_with_supervisor_cookie_and_draft_flag(app, supervisor_client):
    code, days, tid, _ = setup_week(app)
    employee = login(app)
    assert publish(supervisor_client, code).get_json()['versao'] == 1
    calendar_url = employee.get('/api/cal-link').get_json()['url']
    _stage(app, supervisor_client, code, days, tid)
    result = supervisor_client.get(calendar_url, query_string={'rascunho': '1', 'supervisor': '1'})
    assert result.status_code == 200
    assert b'SECRET-DRAFT-ORIGINAL' in result.data
    assert b'PRIVATE-AMENDMENT-AFTER-PAUSE' not in result.data
    assert b'SEQUENCE:1' in result.data
    anonymous = app.app.test_client()
    assert anonymous.get(f'/api/publicacao/{code}', query_string={'rascunho': '1', 'supervisor': '1'}).status_code == 401
    assert anonymous.get('/supervisor/exportar-horas-csv', query_string={'semana': code, 'rascunho': '1'}).status_code == 302
