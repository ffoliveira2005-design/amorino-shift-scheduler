"""Admin recipient selection, atomic donation transfers and SQLite races.

Every test uses test_basics' disposable database. The concurrency checks remove
the process schedule lock deliberately: the database must enforce decisions.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, datetime, timedelta, timezone
import json
import os
import sys
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from flask import request
from sqlalchemy import event

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, _CsrfClient  # noqa: F401 (fixture)
from models import AuditLog, PublishedSemana, PublishedTurno, ScheduleRelease, SchemaMigration
import helpers.publication as publication
import helpers.donations as donations


PIN = 'Secure-8317'


def _login(mod, sigla='ADMIN'):
    client = _CsrfClient(mod.app.test_client(), mod)
    assert client.post('/login', data={'sigla': sigla,
        'pin': '1234' if sigla == 'ADMIN' else PIN}).status_code == 302
    return client


def _setup(mod):
    """One published donor shift, three free candidates, an unrelated shift."""
    monday = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=2)
    days = mod.week_from_monday(monday)
    with mod.app.app_context():
        code = mod.resolve_week_codigo(monday, mod.SemanaConfig.query.all())
        week = mod.SemanaConfig.query.filter_by(codigo=code).first()
        if week is None:
            week = mod.SemanaConfig(codigo=code, label=mod.week_label(monday),
                dias_json=json.dumps(days), ativa=False)
            mod.db.session.add(week)
        hashed_pin = mod.hash_pin(PIN)
        for sigla in ('ZDA', 'ZDB', 'ZDC', 'ZDD'):
            mod.db.session.add(mod.Funcionario(sigla=sigla, nome=f'Pessoa {sigla}',
                pin=hashed_pin, horas_contrato=40, tipo_contrato='contrato'))
        origin = mod.Turno(semana=code, dia=days[0]['label'], loja='gaia',
            horario='10:00-18:30', funcionario='ZDA', nota='DONOR-AUTHORISED')
        unrelated = mod.Turno(semana=code, dia=days[2]['label'], loja='gaia',
            horario='10:00-18:30', funcionario='ZDD', nota='UNRELATED-AUTHORISED')
        mod.db.session.add_all([origin, unrelated])
        mod.db.session.commit()
        origin_id, unrelated_id = origin.id, unrelated.id
        mod.release_week(code, 'ADMIN', 'Escala autorizada para teste de doação')
        mod.db.session.commit()
    clients = {sigla: _login(mod, sigla) for sigla in ('ZDA', 'ZDB', 'ZDC', 'ZDD')}
    clients['ADMIN'] = _login(mod)
    return clients, code, days, origin_id, unrelated_id


def _open(clients, origin_id):
    response = clients['ZDA'].post('/api/trocas/abrir', json={
        'turno_origem_id': origin_id, 'tipo': 'aberto_doacao', 'motivo': 'Doação de turno'})
    assert response.status_code == 200, response.get_json()
    return response.get_json()['troca']['id']


def _apply(clients, tid, sigla):
    response = clients[sigla].post(f'/api/trocas/{tid}/candidatar',
        json={'comentario': f'Disponibilidade de {sigla}'})
    assert response.status_code == 200, response.get_json()
    return response.get_json()['candidatura']['id']


def _read(mod, tid, origin_id):
    with mod.app.app_context():
        pedido = mod.db.session.get(mod.TrocaPedido, tid)
        return {'estado': pedido.estado, 'para_sigla': pedido.para_sigla,
            'draft_owner': getattr(mod.db.session.get(mod.Turno, origin_id), 'funcionario', None),
            'published_owner': getattr(mod.db.session.get(PublishedTurno, origin_id), 'funcionario', None),
            'candidates': {c.sigla: c.status for c in mod.TrocaCandidatura.query.filter_by(troca_id=tid)},
            'decision': pedido.decisao_em, 'reply': pedido.resposta_supervisor}


def _badge(client):
    return client.get('/api/trocas/contagem').get_json()['pendentes']


def test_first_acceptance_enters_admin_queue_and_further_candidates_remain_possible(app):
    clients, _, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    before = _badge(clients['ADMIN'])
    first = _apply(clients, tid, 'ZDB')
    state = _read(app, tid, origin)
    assert state['estado'] == 'pendente_supervisor' and state['para_sigla'] == ''
    assert state['draft_owner'] == state['published_owner'] == 'ZDA'
    assert state['candidates'] == {'ZDB': 'pendente'} and state['decision'] is None
    assert _badge(clients['ADMIN']) == before + 1
    second = _apply(clients, tid, 'ZDC')
    central = clients['ADMIN'].get('/api/central').get_json()['pedidos']
    donation = next(p for p in central if p['id'] == tid)
    assert donation['para_sigla'] == '' and donation['n_candidaturas'] == 2
    assert {c['id'] for c in donation['candidaturas']} == {first, second}
    assert all(c['criado_em'].endswith('+00:00') for c in donation['candidaturas'])
    admin_page = clients['ADMIN'].get('/trocas').get_data(as_text=True)
    assert f'id="card-{tid}"' in admin_page
    assert 'Pessoa ZDB' in admin_page and 'Pessoa ZDC' in admin_page
    employee_page = clients['ZDB'].get('/trocas').get_data(as_text=True)
    assert 'A aguardar decisão da administração' in employee_page
    assert _badge(clients['ADMIN']) == before + 1  # one donation, two choices


def test_admin_selects_one_candidate_and_records_final_history_and_news(app):
    clients, code, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    _apply(clients, tid, 'ZDB')
    selected = _apply(clients, tid, 'ZDC')
    for sigla in ('ZDA', 'ZDB', 'ZDC'):
        clients[sigla].get('/trocas')
    result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir', json={
        'aprovar': True, 'candidatura_id': selected, 'nota': 'Confirmado pela administração'})
    assert result.status_code == 200, result.get_json()
    state = _read(app, tid, origin)
    assert state['estado'] == 'aprovado' and state['para_sigla'] == 'ZDC'
    assert state['draft_owner'] == state['published_owner'] == 'ZDC'
    assert state['candidates'] == {'ZDB': 'rejeitada', 'ZDC': 'aceite'}
    assert state['decision'] is not None and state['reply'] == 'Confirmado pela administração'
    assert all(_badge(clients[s]) >= 1 for s in ('ZDA', 'ZDB', 'ZDC'))
    assert not any(p['id'] == tid for p in clients['ZDB'].get('/api/central').get_json()['pedidos'])
    body = clients['ZDA'].get('/trocas').get_data(as_text=True)
    assert 'Pessoa ZDC' in body and 'Confirmado pela administração' in body
    with app.app.app_context():
        releases = ScheduleRelease.query.filter_by(semana=code).order_by(ScheduleRelease.version).all()
        assert [r.version for r in releases] == [1, 2]
        approvals = AuditLog.query.filter_by(action='troca_aprovar', target=str(tid)).all()
        assert len(approvals) == 1 and approvals[0].actor == 'ADMIN'
        assert 'ZDA' in approvals[0].detail and 'ZDC' in approvals[0].detail
        assert str(selected) in approvals[0].detail and str(origin) in approvals[0].detail


def test_duplicate_application_is_rejected_and_last_withdrawal_reopens_donation(app):
    clients, _, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    _apply(clients, tid, 'ZDB')
    assert clients['ZDB'].post(f'/api/trocas/{tid}/candidatar', json={}).status_code == 409
    _apply(clients, tid, 'ZDC')
    assert clients['ZDB'].post(f'/api/trocas/{tid}/retirar-candidatura').status_code == 200
    assert _read(app, tid, origin)['estado'] == 'pendente_supervisor'
    assert clients['ZDC'].post(f'/api/trocas/{tid}/retirar-candidatura').status_code == 200
    state = _read(app, tid, origin)
    assert state['estado'] == 'aberto' and state['para_sigla'] == ''
    assert state['candidates'] == {'ZDB': 'retirada', 'ZDC': 'retirada'}
    assert clients['ZDB'].post(f'/api/trocas/{tid}/retirar-candidatura').status_code in (404, 409)
    _apply(clients, tid, 'ZDB')
    assert _read(app, tid, origin)['estado'] == 'pendente_supervisor'


@pytest.mark.parametrize('action, expected, candidate_status', [
    ('reject', 'negado', 'negada'), ('cancel', 'cancelado', 'cancelada')])
def test_whole_donation_can_be_rejected_or_cancelled_before_final_approval(app, action, expected, candidate_status):
    clients, _, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    chosen = _apply(clients, tid, 'ZDB')
    _apply(clients, tid, 'ZDC')
    clients['ZDB'].get('/trocas')
    if action == 'reject':
        response = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir', json={
            'aprovar': False, 'nota': 'Manter a equipa prevista'})
    else:
        response = clients['ZDA'].post(f'/api/trocas/{tid}/cancelar')
    assert response.status_code == 200, response.get_json()
    state = _read(app, tid, origin)
    assert state['estado'] == expected and state['para_sigla'] == ''
    assert state['draft_owner'] == state['published_owner'] == 'ZDA'
    assert set(state['candidates'].values()) == {candidate_status}
    assert _badge(clients['ZDB']) >= 1
    assert clients['ADMIN'].post(f'/api/trocas/{tid}/decidir', json={
        'aprovar': True, 'candidatura_id': chosen}).status_code == 409


def test_approval_requires_pending_candidate_of_that_specific_donation(app):
    clients, _, _, origin, unrelated = _setup(app)
    tid = _open(clients, origin)
    own = _apply(clients, tid, 'ZDB')
    other_response = clients['ZDD'].post('/api/trocas/abrir', json={
        'turno_origem_id': unrelated, 'tipo': 'aberto_doacao'})
    assert other_response.status_code == 200
    foreign = _apply(clients, other_response.get_json()['troca']['id'], 'ZDC')
    for payload in ({'aprovar': True}, {'aprovar': True, 'candidatura_id': 'invalid'},
                    {'aprovar': True, 'candidatura_id': foreign},
                    {'aprovar': True, 'candidatura_id': 999999}):
        result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir', json=payload)
        assert result.status_code in (400, 404, 409), result.get_json()
        assert _read(app, tid, origin)['para_sigla'] == ''
    assert clients['ZDB'].post(f'/api/trocas/{tid}/retirar-candidatura').status_code == 200
    _apply(clients, tid, 'ZDC')  # keep the donation in the admin queue
    result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': own})
    assert result.status_code == 409
    assert _read(app, tid, origin)['published_owner'] == 'ZDA'


def test_direct_donation_cannot_bypass_admin_recipient_selection(app):
    clients, _, _, origin, _ = _setup(app)
    result = clients['ZDA'].post('/api/trocas', json={
        'turno_origem_id': origin, 'para_sigla': 'ZDB'})
    assert result.status_code == 200, result.get_json()
    donation = result.get_json()['troca']
    tid = donation['id']
    assert donation['tipo'] == 'aberto_doacao' and donation['para_sigla'] == ''
    assert donation['estado'] == 'aberto'
    assert clients['ZDB'].post(f'/api/trocas/{tid}/responder', json={'aceitar': True}).status_code == 409
    assert clients['ADMIN'].post(f'/api/trocas/{tid}/decidir', json={'aprovar': True}).status_code in (400, 409)
    assert _read(app, tid, origin)['published_owner'] == 'ZDA'


def test_genuine_open_exchange_preserves_author_choice_and_swaps_both_shifts(app):
    clients, _, _, origin, offered = _setup(app)
    response = clients['ZDA'].post('/api/trocas/abrir', json={
        'turno_origem_id': origin, 'tipo': 'aberto_troca'})
    assert response.status_code == 200
    tid = response.get_json()['troca']['id']
    response = clients['ZDD'].post(f'/api/trocas/{tid}/candidatar',
        json={'turno_oferecido_id': offered})
    assert response.status_code == 200, response.get_json()
    cid = response.get_json()['candidatura']['id']
    assert _read(app, tid, origin)['estado'] == 'aberto'
    choice = clients['ZDA'].post(f'/api/trocas/{tid}/escolher', json={'candidatura_id': cid})
    assert choice.status_code == 200, choice.get_json()
    assert _read(app, tid, origin)['para_sigla'] == 'ZDD'
    response = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir', json={'aprovar': True})
    assert response.status_code == 200, response.get_json()
    assert _read(app, tid, origin)['published_owner'] == 'ZDD'
    with app.app.app_context():
        assert app.db.session.get(app.Turno, offered).funcionario == 'ZDA'
        assert app.db.session.get(PublishedTurno, offered).funcionario == 'ZDA'


def test_candidate_conflicts_are_checked_when_applying_and_again_when_approving(app):
    clients, code, days, origin, _ = _setup(app)
    tid = _open(clients, origin)
    with app.app.app_context():
        clash = app.Turno(semana=code, dia=days[0]['label'], loja='carmelitas',
            horario='12:00-20:00', funcionario='ZDB')
        app.db.session.add(clash)
        app.db.session.commit()
        app.release_week(code, 'ADMIN', 'Conflito autorizado para validação')
        app.db.session.commit()
    blocked = clients['ZDB'].post(f'/api/trocas/{tid}/candidatar', json={})
    assert blocked.status_code == 409 and blocked.get_json()['conflito'] is True
    candidate = _apply(clients, tid, 'ZDC')
    with app.app.app_context():
        app.db.session.add(app.Turno(semana=code, dia=days[0]['label'], loja='carmelitas',
            horario='12:00-20:00', funcionario='ZDC'))
        app.db.session.commit()  # a draft conflict also blocks a final transfer
    blocked = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': candidate})
    assert blocked.status_code == 409 and blocked.get_json()['conflito'] is True
    assert _read(app, tid, origin)['published_owner'] == 'ZDA'


def test_candidate_advisory_warnings_are_visible_and_do_not_block_admin_approval(app):
    clients, code, days, origin, _ = _setup(app)
    tid = _open(clients, origin)
    with app.app.app_context():
        app.db.session.get(app.Funcionario, 'ZDB').horas_contrato = 4
        app.db.session.add(app.Disponibilidade(semana=code, funcionario='ZDB',
            dia=days[0]['label'], tipo='indisponivel'))
        app.db.session.commit()
    candidate = _apply(clients, tid, 'ZDB')
    central = clients['ADMIN'].get('/api/central').get_json()['pedidos']
    serialized = next(p for p in central if p['id'] == tid)['candidaturas'][0]
    assert not serialized['fatal'] and not serialized['conflitos']
    assert any('indisponível' in warning for warning in serialized['avisos'])
    assert any('contrato' in warning for warning in serialized['avisos'])
    result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': candidate})
    assert result.status_code == 200, result.get_json()
    assert _read(app, tid, origin)['published_owner'] == 'ZDB'


@pytest.mark.parametrize('stage', ['application', 'approval'])
@pytest.mark.parametrize('representation', ['draft', 'published'])
@pytest.mark.parametrize('authorised_store_transfer', [False, True])
def test_donation_obeys_other_store_rule_at_application_and_approval(
        app, stage, representation, authorised_store_transfer):
    """Separated shifts in different stores still require the existing note marker."""
    clients, code, days, origin, _ = _setup(app)
    if authorised_store_transfer:
        with app.app.app_context():
            app.db.session.get(app.Turno, origin).nota = 'Troca de loja autorizada'
            app.db.session.commit()
            app.release_week(code, 'ADMIN', 'Mudança de loja autorizada na nota')
            app.db.session.commit()
    tid = _open(clients, origin)
    cid = _apply(clients, tid, 'ZDB') if stage == 'approval' else None
    with app.app.app_context():
        existing = app.Turno(semana=code, dia=days[0]['label'], loja='carmelitas',
            horario='08:00-09:00', funcionario='ZDB')
        app.db.session.add(existing)
        app.db.session.commit()
        if representation == 'published':
            app.release_week(code, 'ADMIN', 'Turno noutra loja já publicado')
            app.db.session.commit()
            # The employee's other-store shift is absent only in the draft;
            # its authorised assignment must still be checked independently.
            app.db.session.delete(existing)
            app.db.session.commit()
    before = _read(app, tid, origin)
    if stage == 'application':
        result = clients['ZDB'].post(f'/api/trocas/{tid}/candidatar', json={})
    else:
        result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
            json={'aprovar': True, 'candidatura_id': cid})
    if not authorised_store_transfer:
        assert result.status_code == 409, result.get_json()
        assert result.get_json()['conflito'] is True
        assert 'Troca de loja' in result.get_json()['erro']
        assert _read(app, tid, origin) == before
    else:
        assert result.status_code == 200, result.get_json()
        if stage == 'application':
            cid = result.get_json()['candidatura']['id']
            final = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
                json={'aprovar': True, 'candidatura_id': cid})
            assert final.status_code == 200, final.get_json()
        assert _read(app, tid, origin)['draft_owner'] == 'ZDB'
        assert _read(app, tid, origin)['published_owner'] == 'ZDB'


def test_removed_candidate_is_ineligible_at_final_approval(app):
    clients, _, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    candidate = _apply(clients, tid, 'ZDB')
    with app.app.app_context():
        app.db.session.delete(app.db.session.get(app.Funcionario, 'ZDB'))
        app.db.session.commit()
    result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': candidate})
    assert result.status_code == 409
    assert _read(app, tid, origin)['published_owner'] == 'ZDA'


@pytest.mark.parametrize('drift', ['draft_remove', 'published_material_change'])
def test_removed_or_materially_changed_shift_invalidates_pending_donation(app, drift):
    clients, code, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    candidate = _apply(clients, tid, 'ZDB')
    with app.app.app_context():
        shift = app.db.session.get(app.Turno, origin)
        if drift == 'draft_remove':
            app.db.session.delete(shift)
        else:
            shift.horario = '11:00-19:30'
        app.db.session.commit()
        if drift == 'published_material_change':
            app.release_week(code, 'ADMIN', 'Alteração material autorizada do turno')
            app.db.session.commit()
    response = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': candidate})
    assert response.status_code == 409
    assert _read(app, tid, origin)['estado'] == 'expirado'
    assert _read(app, tid, origin)['published_owner'] == 'ZDA'


def test_donation_approval_publishes_only_transfer_and_keeps_unrelated_draft_private(app):
    clients, code, _, origin, unrelated = _setup(app)
    tid = _open(clients, origin)
    candidate = _apply(clients, tid, 'ZDB')
    with app.app.app_context():
        app.db.session.get(app.Turno, unrelated).nota = 'UNRELATED-PRIVATE-DRAFT'
        app.db.session.get(app.SemanaConfig, app.SemanaConfig.query.filter_by(codigo=code).first().id).label = 'PRIVATE-WEEK-LABEL'
        app.db.session.commit()
    result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': candidate})
    assert result.status_code == 200, result.get_json()
    with app.app.app_context():
        assert app.db.session.get(app.Turno, unrelated).nota == 'UNRELATED-PRIVATE-DRAFT'
        assert app.db.session.get(PublishedTurno, unrelated).nota == 'UNRELATED-AUTHORISED'
        final = json.loads(app.latest_release(code).snapshot_json)
        assert final['week']['label'] != 'PRIVATE-WEEK-LABEL'
        assert 'UNRELATED-PRIVATE-DRAFT' not in json.dumps(final)
        transfer = json.loads(app.latest_release(code).changes_json)
        assert len(transfer) == 1 and transfer[0]['before']['funcionario'] == 'ZDA'
        assert transfer[0]['after']['funcionario'] == 'ZDB'
    employee_page = clients['ZDB'].get('/escala', query_string={'semana': code}).data
    assert b'UNRELATED-PRIVATE-DRAFT' not in employee_page and b'PRIVATE-WEEK-LABEL' not in employee_page


def test_failed_publication_rolls_back_selection_transfer_candidate_outcomes_and_audit(app):
    clients, code, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    selected = _apply(clients, tid, 'ZDB')
    _apply(clients, tid, 'ZDC')
    before = _read(app, tid, origin)
    with app.app.app_context():
        engine = app.db.engine

    def fail_projection(connection, cursor, statement, parameters, context, executemany):
        if statement.lower().strip().startswith('insert into published_turnos'):
            raise RuntimeError('Donation publication storage failure')

    event.listen(engine, 'before_cursor_execute', fail_projection)
    try:
        try:
            result = clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
                json={'aprovar': True, 'candidatura_id': selected})
        except RuntimeError as exc:
            assert 'publication storage failure' in str(exc)
        else:
            assert result.status_code == 500
    finally:
        event.remove(engine, 'before_cursor_execute', fail_projection)
    assert _read(app, tid, origin) == before
    with app.app.app_context():
        assert ScheduleRelease.query.filter_by(semana=code).count() == 1
        assert not AuditLog.query.filter_by(action='troca_aprovar', target=str(tid)).first()
    assert clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': selected}).status_code == 200


class _NoProcessLock:
    def acquire(self, *args, **kwargs):
        return True

    def release(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _race(first, second):
    barrier = threading.Barrier(3)

    def run(action):
        barrier.wait(timeout=8)
        return action()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run, first), pool.submit(run, second)
        barrier.wait(timeout=8)
        return a.result(timeout=15), b.result(timeout=15)


def test_two_admins_cannot_transfer_donation_twice_without_process_lock(app, monkeypatch):
    clients, code, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    first = _apply(clients, tid, 'ZDB')
    second = _apply(clients, tid, 'ZDC')
    competing_admin = _login(app)
    monkeypatch.setattr(publication, 'schedule_lock', _NoProcessLock())
    responses = _race(
        lambda: clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
            json={'aprovar': True, 'candidatura_id': first}),
        lambda: competing_admin.post(f'/api/trocas/{tid}/decidir',
            json={'aprovar': True, 'candidatura_id': second}))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.get_json() for r in responses]
    state = _read(app, tid, origin)
    assert state['estado'] == 'aprovado'
    assert state['draft_owner'] == state['published_owner'] == state['para_sigla']
    assert sorted(state['candidates'].values()) == ['aceite', 'rejeitada']
    with app.app.app_context():
        assert [r.version for r in ScheduleRelease.query.filter_by(semana=code).order_by(ScheduleRelease.version)] == [1, 2]
        assert AuditLog.query.filter_by(action='troca_aprovar', target=str(tid)).count() == 1
    assert clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': first}).status_code == 409


def test_admin_holds_sqlite_writer_reservation_through_validation_and_commit(app, monkeypatch):
    """An independent DB connection and another admin must wait for the decision."""
    clients, code, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    first = _apply(clients, tid, 'ZDB')
    second = _apply(clients, tid, 'ZDC')
    competing_admin = _login(app)
    monkeypatch.setattr(publication, 'schedule_lock', _NoProcessLock())
    reached, resume, competing_started = threading.Event(), threading.Event(), threading.Event()
    validate = app._donation_candidate_problems

    def paused_validation(pedido, sigla):
        if request.endpoint == 'api_troca_decidir' and sigla == 'ZDB':
            reached.set()
            assert resume.wait(timeout=8), 'Approval did not resume'
        return validate(pedido, sigla)

    monkeypatch.setattr(app, '_donation_candidate_problems', paused_validation)
    with app.app.app_context():
        database_path = app.db.engine.url.database

    def competing_approval():
        competing_started.set()
        return competing_admin.post(f'/api/trocas/{tid}/decidir',
            json={'aprovar': True, 'candidatura_id': second})

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(clients['ADMIN'].post, f'/api/trocas/{tid}/decidir',
            json={'aprovar': True, 'candidatura_id': first})
        loser = None
        try:
            assert reached.wait(timeout=8), 'Approval did not reach authoritative validation'
            external_connection = sqlite3.connect(database_path, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    external_connection.execute('BEGIN IMMEDIATE')
            finally:
                external_connection.close()
            loser = pool.submit(competing_approval)
            assert competing_started.wait(timeout=8)
            with pytest.raises(TimeoutError):
                loser.result(timeout=0.15)
        finally:
            resume.set()
        assert winner.result(timeout=8).status_code == 200
        assert loser is not None and loser.result(timeout=8).status_code == 409
    state = _read(app, tid, origin)
    assert state['draft_owner'] == state['published_owner'] == state['para_sigla'] == 'ZDB'
    assert state['candidates'] == {'ZDB': 'aceite', 'ZDC': 'rejeitada'}
    with app.app.app_context():
        assert ScheduleRelease.query.filter_by(semana=code).count() == 2


@pytest.mark.parametrize('competing_action', ['cancel', 'withdraw'])
def test_approval_racing_cancellation_or_withdrawal_has_one_consistent_outcome(app, monkeypatch, competing_action):
    clients, code, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    candidate = _apply(clients, tid, 'ZDB')
    monkeypatch.setattr(publication, 'schedule_lock', _NoProcessLock())
    competing = (lambda: clients['ZDA'].post(f'/api/trocas/{tid}/cancelar')) if competing_action == 'cancel' else (
        lambda: clients['ZDB'].post(f'/api/trocas/{tid}/retirar-candidatura'))
    approval, other = _race(lambda: clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': candidate}), competing)
    state = _read(app, tid, origin)
    assert (approval.status_code == 200) != (other.status_code == 200)
    if approval.status_code == 200:
        assert state['estado'] == 'aprovado' and state['para_sigla'] == 'ZDB'
        assert state['draft_owner'] == state['published_owner'] == 'ZDB'
        assert state['candidates'] == {'ZDB': 'aceite'}
        assert other.status_code in (404, 409)
    else:
        assert approval.status_code == 409
        assert state['estado'] == ('cancelado' if competing_action == 'cancel' else 'aberto')
        assert state['para_sigla'] == '' and state['draft_owner'] == state['published_owner'] == 'ZDA'
        assert state['candidates']['ZDB'] == ('cancelada' if competing_action == 'cancel' else 'retirada')
    with app.app.app_context():
        assert ScheduleRelease.query.filter_by(semana=code).count() == (2 if approval.status_code == 200 else 1)


def test_simultaneous_duplicate_applications_create_only_one_active_candidate(app, monkeypatch):
    clients, _, _, origin, _ = _setup(app)
    tid = _open(clients, origin)
    competing = _login(app, 'ZDB')
    monkeypatch.setattr(publication, 'schedule_lock', _NoProcessLock())
    responses = _race(lambda: clients['ZDB'].post(f'/api/trocas/{tid}/candidatar', json={}),
        lambda: competing.post(f'/api/trocas/{tid}/candidatar', json={}))
    assert sorted(r.status_code for r in responses) == [200, 409]
    with app.app.app_context():
        assert app.TrocaCandidatura.query.filter_by(troca_id=tid, sigla='ZDB', status='pendente').count() == 1
    assert _read(app, tid, origin)['estado'] == 'pendente_supervisor'


@pytest.mark.parametrize('with_candidate', [False, True])
def test_unassigned_donation_expires_at_shift_start_including_admin_pending(app, monkeypatch, with_candidate):
    from zoneinfo import ZoneInfo
    clients, _, days, origin, _ = _setup(app)
    tid = _open(clients, origin)
    cid = _apply(clients, tid, 'ZDB') if with_candidate else None
    starts = datetime.fromisoformat(days[0]['date']).replace(
        hour=10, tzinfo=ZoneInfo('Europe/Lisbon')).astimezone(timezone.utc)
    monkeypatch.setattr(app, 'shift_started', lambda shift:
        donations.shift_started(shift, now=starts - timedelta(microseconds=1)))
    assert any(p['id'] == tid for p in clients['ZDC'].get('/api/central').get_json()['pedidos'])
    monkeypatch.setattr(app, 'shift_started', lambda shift:
        donations.shift_started(shift, now=starts))
    assert not any(p['id'] == tid for p in clients['ZDC'].get('/api/central').get_json()['pedidos'])
    state = _read(app, tid, origin)
    assert state['estado'] == 'expirado' and state['para_sigla'] == ''
    assert state['draft_owner'] == state['published_owner'] == 'ZDA'
    if cid:
        assert state['candidates'] == {'ZDB': 'expirada'}
        assert clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
            json={'aprovar': True, 'candidatura_id': cid}).status_code == 409
    assert clients['ZDC'].post(f'/api/trocas/{tid}/candidatar', json={}).status_code == 409


@pytest.mark.parametrize('day, utc_hour', [('2027-01-18', 10), ('2027-07-19', 9)])
def test_shift_start_uses_lisbon_offset_in_winter_and_summer(monkeypatch, day, utc_hour):
    monkeypatch.setattr(donations, 'shift_identity', lambda shift: {'date': day})
    shift = SimpleNamespace(horario='10:00-18:30')
    boundary = datetime.fromisoformat(day).replace(hour=utc_hour, tzinfo=timezone.utc)
    assert not donations.shift_started(shift, now=boundary - timedelta(microseconds=1))
    assert donations.shift_started(shift, now=boundary)


@pytest.mark.parametrize('days_json', [
    '{', '{}', '"legacy"', '[null,"legacy",3]',
    '[{"label":"Seg 18","date":"2027-99-00"}]',
    '[{"label":"Seg 18","date":173}]'])
def test_malformed_published_week_dates_cannot_authorise_a_shift(monkeypatch, days_json):
    shift = SimpleNamespace(id=1, semana='legacy', dia='Seg 18', loja='gaia',
        horario='10:00-18:30', funcionario='ZDA', nota='')
    week = SimpleNamespace(dias_json=days_json)
    monkeypatch.setattr(donations, 'db', SimpleNamespace(
        session=SimpleNamespace(get=lambda model, identifier: week)))
    assert donations.shift_identity(shift)['date'] == ''
    assert donations.shift_started(shift, now=datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize('horario', [None, '', '24:00-02:00', '09:60-11:00', 'texto'])
def test_malformed_start_clocks_cannot_keep_a_donation_open(monkeypatch, horario):
    monkeypatch.setattr(donations, 'shift_identity', lambda shift: {'date': '2027-01-18'})
    assert donations.shift_started(SimpleNamespace(horario=horario),
        now=datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize('malformation', ['dict', 'string', 'invalid_date'])
def test_corrupted_published_week_expires_donation_and_keeps_history_usable(app, malformation):
    clients, code, days, origin, _ = _setup(app)
    tid = _open(clients, origin)
    cid = _apply(clients, tid, 'ZDB')
    with app.app.app_context():
        week = app.db.session.get(PublishedSemana, code)
        if malformation == 'dict':
            week.dias_json = json.dumps({'label': days[0]['label'], 'date': days[0]['date']})
        elif malformation == 'string':
            week.dias_json = json.dumps('legacy-date')
        else:
            invalid_days = [{**day} for day in days]
            invalid_days[0]['date'] = '2027-99-00'
            week.dias_json = json.dumps(invalid_days)
        app.db.session.commit()
    central = clients['ZDC'].get('/api/central')
    assert central.status_code == 200
    assert not any(p['id'] == tid for p in central.get_json()['pedidos'])
    state = _read(app, tid, origin)
    assert state['estado'] == 'expirado' and state['para_sigla'] == ''
    assert state['candidates'] == {'ZDB': 'expirada'}
    assert state['draft_owner'] == state['published_owner'] == 'ZDA'
    assert clients['ADMIN'].post(f'/api/trocas/{tid}/decidir',
        json={'aprovar': True, 'candidatura_id': cid}).status_code == 409
    for sigla in ('ZDA', 'ZDB', 'ADMIN'):
        history = clients[sigla].get('/trocas')
        assert history.status_code == 200
        body = history.get_data(as_text=True)
        if sigla == 'ZDB':
            assert 'Candidatura a Pessoa ZDA' in body and 'Doação expirada' in body
        else:
            assert f'id="card-{tid}"' in body


def test_legacy_donation_migration_is_idempotent_and_preserves_completed_history(app):
    clients, code, _, origin, _ = _setup(app)
    completed_at = datetime(2026, 8, 1, 12, 30)
    with app.app.app_context():
        marker = app.db.session.get(SchemaMigration, 'donation_admin_review_v1')
        app.db.session.delete(marker)
        pending = app.TrocaPedido(de_sigla='ZDA', para_sigla='ZDB', turno_origem_id=origin,
            tipo='aberto_doacao', estado='pendente_supervisor', decisao_em=completed_at)
        direct_accepted = app.TrocaPedido(de_sigla='ZDA', para_sigla='ZDC', turno_origem_id=origin,
            tipo='direto', estado='pendente_supervisor', resposta_colega='Disponível')
        direct_nomination = app.TrocaPedido(de_sigla='ZDA', para_sigla='ZDD', turno_origem_id=origin,
            tipo='direto', estado='pendente_colega')
        completed = app.TrocaPedido(de_sigla='ZDA', para_sigla='ZDB', turno_origem_id=origin,
            tipo='aberto_doacao', estado='aprovado', decisao_em=completed_at,
            resposta_supervisor='Decisão histórica preservada')
        app.db.session.add_all([pending, direct_accepted, direct_nomination, completed])
        app.db.session.flush()
        ids = [p.id for p in (pending, direct_accepted, direct_nomination, completed)]
        app.db.session.add_all([
            app.TrocaCandidatura(troca_id=pending.id, sigla='ZDB', status='aceite'),
            app.TrocaCandidatura(troca_id=pending.id, sigla='ZDC', status='rejeitada'),
            app.TrocaCandidatura(troca_id=pending.id, sigla='ZDD', status='retirada'),
            app.TrocaCandidatura(troca_id=completed.id, sigla='ZDB', status='aceite')])
        app.db.session.commit()
        assert donations.migration_donation_review() == 3
        app.db.session.expire_all()
        changed = [app.db.session.get(app.TrocaPedido, pid) for pid in ids]
        assert all(p.tipo == 'aberto_doacao' and p.para_sigla == '' for p in changed[:3])
        assert [p.estado for p in changed[:3]] == ['pendente_supervisor', 'pendente_supervisor', 'aberto']
        assert all(p.decisao_em is None and json.loads(p.origem_snapshot_json)['id'] == origin for p in changed[:3])
        assert {c.sigla: c.status for c in app.TrocaCandidatura.query.filter_by(troca_id=ids[0])} == {
            'ZDB': 'pendente', 'ZDC': 'pendente', 'ZDD': 'retirada'}
        accepted = app.TrocaCandidatura.query.filter_by(troca_id=ids[1]).one()
        assert accepted.sigla == 'ZDC' and accepted.status == 'pendente'
        assert not app.TrocaCandidatura.query.filter_by(troca_id=ids[2]).count()
        historical = changed[3]
        assert (historical.tipo, historical.estado, historical.para_sigla, historical.decisao_em,
            historical.resposta_supervisor) == ('aberto_doacao', 'aprovado', 'ZDB', completed_at,
                'Decisão histórica preservada')
        assert app.TrocaCandidatura.query.filter_by(troca_id=ids[3]).one().status == 'aceite'
        assert app.db.session.get(app.Turno, origin).funcionario == 'ZDA'
        assert app.db.session.get(PublishedTurno, origin).funcionario == 'ZDA'
        assert ScheduleRelease.query.filter_by(semana=code).count() == 1
        assert AuditLog.query.filter_by(action='doacao_migrar').count() == 3
        candidates_count = app.TrocaCandidatura.query.count()
        assert donations.migration_donation_review() == 0
        assert app.TrocaCandidatura.query.count() == candidates_count
        assert AuditLog.query.filter_by(action='doacao_migrar').count() == 3
