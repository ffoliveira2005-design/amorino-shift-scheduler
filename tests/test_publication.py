"""Authorised schedule boundaries, staged amendments and migration on scratch data."""
import base64
from datetime import date, timedelta
import json
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client, employee_client, _CsrfClient
from models import PublishedTurno, PublishedDescarga, ScheduleRelease, ScheduleChange, SchemaMigration


def setup_week(mod):
    with mod.app.app_context():
        start = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=2)
        days = mod.week_from_monday(start)
        code = mod.week_codigo(start)
        mod.db.session.add(mod.SemanaConfig(codigo=code, label=mod.week_label(start),
            dias_json=json.dumps(days), ativa=False,
            descarga_config=json.dumps({days[1]['label']: {'gaia': '08:00-10:00'}})))
        for sig in ('ZAA', 'ZBB'):
            mod.db.session.add(mod.Funcionario(sigla=sig, nome=f'Fictício {sig}',
                horas_contrato=40, pin=mod.hash_pin('Secure-8317')))
        shift = mod.Turno(semana=code, dia=days[0]['label'], loja='gaia',
            horario='16:00-00:30 (+1)', funcionario='ZAA', nota='SECRET-DRAFT-ORIGINAL')
        mod.db.session.add(shift)
        delivery = mod.Descarga(semana=code, dia=days[1]['label'], loja='gaia',
            horario='08:00-10:00', funcionario='ZAA')
        mod.db.session.add(delivery)
        mod.db.session.commit()
        return code, days, shift.id, delivery.id


def login(mod, sig='ZAA'):
    c = _CsrfClient(mod.app.test_client(), mod)
    assert c.post('/login', data={'sigla': sig, 'pin': 'Secure-8317'}).status_code == 302
    return c


def publish(sup, code, reason='Revisão e comunicação privada da versão'):
    info = sup.get(f'/api/publicacao/{code}').get_json()
    return sup.post(f'/api/publicacao/{code}', json={'confirmar': True,
        'versao_esperada': info['versao'], 'rascunho_hash': info['rascunho_hash'], 'motivo': reason})


def pdf_text(body):
    streams = re.findall(rb'stream\r?\n(.*?)endstream', body, re.S)
    result = []
    for stream in streams:
        try:
            raw = base64.a85decode(stream.strip(), adobe=True)
            result.append(zlib.decompress(raw).decode('latin-1'))
        except (ValueError, zlib.error):
            result.append(stream.decode('latin-1'))
    return '\n'.join(result)


def test_draft_is_absent_from_every_employee_route_and_knowledge_of_id(app, supervisor_client):
    code, days, tid, _ = setup_week(app)
    emp = login(app)
    for path in ('/', '/escala', '/minha-escala', '/equipa', '/trocas'):
        result = emp.get(path, query_string={'semana': code, 'rascunho': '1'})
        assert result.status_code == (404 if path == '/escala' else 200)
        assert b'SECRET-DRAFT-ORIGINAL' not in result.data
    assert emp.get(f'/cal/turno/{tid}.ics').status_code == 404
    assert emp.get('/supervisor/exportar-pdf-individual/ZAA', query_string={'semana': code, 'rascunho': '1'}).status_code == 404
    assert emp.post('/api/trocas', json={'turno_origem_id': tid, 'para_sigla': 'ZBB'}).status_code == 404
    assert emp.get('/api/trocas/colegas', query_string={'turno_id': tid}).status_code in (400, 404)
    token = emp.get('/api/cal-link').get_json()['url']
    assert b'SECRET-DRAFT-ORIGINAL' not in emp.get(token).data
    info = emp.get(f'/api/publicacao/{code}').get_json()
    assert info['versao'] == 0 and not info['historico']
    assert 'rascunho_hash' not in info and 'alteracoes_pendentes' not in info
    for path in ('/supervisor/exportar-pdf', '/supervisor/exportar-pdf-loja/gaia', '/supervisor/exportar-horas-csv', '/escala/descargas'):
        assert emp.get(path, query_string={'semana': code}).status_code in (302, 403)


def test_publish_requires_supervisor_csrf_confirmation_and_reviewed_revision(app, supervisor_client):
    code, days, tid, _ = setup_week(app)
    emp = login(app)
    assert emp.post(f'/api/publicacao/{code}', json={'confirmar': True}).status_code == 403
    raw = app.app.test_client()
    raw.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'})
    assert raw.post(f'/api/publicacao/{code}', json={'confirmar': True}).status_code == 403
    old = supervisor_client.get(f'/api/publicacao/{code}').get_json()
    assert supervisor_client.post(f'/api/publicacao/{code}', json={}).status_code == 400
    assert supervisor_client.post(f'/api/turno/move/{tid}', json={'to_dia': days[0]['label'], 'to_loja': 'gaia', 'to_horario': '15:00-23:30'}).status_code == 200
    stale = supervisor_client.post(f'/api/publicacao/{code}', json={'confirmar': True,
        'versao_esperada': old['versao'], 'rascunho_hash': old['rascunho_hash'], 'motivo': 'revisão'})
    assert stale.status_code == 409
    assert publish(supervisor_client, code).get_json()['versao'] == 1
    assert publish(supervisor_client, code).get_json()['versao'] == 1  # no phantom amendments


def test_staged_moves_deletions_deliveries_and_additions_do_not_change_authorised_outputs(app, supervisor_client):
    code, days, tid, did = setup_week(app)
    emp = login(app)
    assert publish(supervisor_client, code).get_json()['versao'] == 1
    calendar_url = emp.get('/api/cal-link').get_json()['url']
    before_ics = emp.get(calendar_url).data
    before_csv = supervisor_client.get('/supervisor/exportar-horas-csv', query_string={'semana': code}).data
    old_pdf = emp.get('/supervisor/exportar-pdf-individual/ZAA', query_string={'semana': code})
    assert 'SECRET-DRAFT-ORIGINAL' in pdf_text(old_pdf.data)
    assert old_pdf.headers['X-Amorino-Schedule-Version'] == 'v1'
    assert supervisor_client.post(f'/api/turno/move/{tid}', json={'to_dia': days[0]['label'], 'to_loja': 'gaia', 'to_horario': '12:00-16:00', 'nota': 'STAGED-NEW'}).status_code == 200
    assert supervisor_client.delete(f'/api/descarga/remove/{did}').status_code == 200
    assert supervisor_client.post('/api/turno/add', json={'semana': code, 'dia': days[2]['label'],
        'loja': 'gaia', 'horario': '10:00-14:00', 'funcionario': 'ZAA', 'nota': 'STAGED-ADDITION'}).status_code == 200
    for path in ('/escala', '/minha-escala'):
        body = emp.get(path, query_string={'semana': code}).data
        assert b'SECRET-DRAFT-ORIGINAL' in body and b'STAGED-' not in body
    assert emp.get(calendar_url).data == before_ics
    assert supervisor_client.get('/supervisor/exportar-horas-csv', query_string={'semana': code}).data == before_csv
    staged_pdf = emp.get('/supervisor/exportar-pdf-individual/ZAA', query_string={'semana': code, 'rascunho': '1'})
    assert 'SECRET-DRAFT-ORIGINAL' in pdf_text(staged_pdf.data) and 'STAGED-NEW' not in pdf_text(staged_pdf.data)
    assert publish(supervisor_client, code, 'Alteração confirmada; informar os intervenientes').get_json()['versao'] == 2
    body = emp.get('/minha-escala', query_string={'semana': code}).data
    assert b'STAGED-NEW' in body and b'STAGED-ADDITION' in body and b'SECRET-DRAFT-ORIGINAL' not in body
    assert b'SEQUENCE:2' in emp.get(calendar_url).data
    assert b'STAGED-NEW' in emp.get(f'/cal/turno/{tid}.ics').data
    history = emp.get(f'/api/publicacao/{code}').get_json()
    assert history['versao'] == 2 and history['responsavel'] == 'ADMIN'
    assert len(history['historico']) == 2
    with app.app.app_context():
        events = ScheduleChange.query.filter_by(semana=code).all()
        changes = [c for e in events for c in json.loads(e.changes_json)]
        assert any(c['operation'] == 'move' and c['before']['horario'] == '16:00-00:30 (+1)' and c['after']['horario'] == '12:00-16:00' for c in changes)
        assert any(c['kind'] == 'deliveries' and c['operation'] == 'remove' for c in changes)
        assert any(c['operation'] == 'add' and c['after'].get('nota') == 'STAGED-ADDITION' for c in changes)
        assert PublishedDescarga.query.filter_by(semana=code).count() == 0


def test_approved_swap_records_amendment_without_releasing_unrelated_draft(app, supervisor_client):
    code, days, tid, _ = setup_week(app)
    a, b = login(app), login(app, 'ZBB')
    publish(supervisor_client, code)
    request_id = a.post('/api/trocas', json={'turno_origem_id': tid, 'para_sigla': 'ZBB'}).get_json()['troca']['id']
    candidacy = b.post(f'/api/trocas/{request_id}/candidatar', json={}).get_json()['candidatura']['id']
    supervisor_client.post('/api/turno/add', json={'semana': code, 'dia': days[3]['label'],
        'loja': 'gaia', 'horario': '10:00-14:00', 'funcionario': 'ZAA', 'nota': 'PRIVATE-STAGED'})
    result = supervisor_client.post(f'/api/trocas/{request_id}/decidir', json={'aprovar': True, 'candidatura_id': candidacy})
    assert result.status_code == 200, result.get_json()
    with app.app.app_context():
        published = app.db.session.get(PublishedTurno, tid)
        assert published.funcionario == 'ZBB' and published.version == 2
        last = app.latest_release(code)
        assert last.reason == f'Doação aprovada #{request_id}'
        assert 'PRIVATE-STAGED' not in last.snapshot_json
    assert b'PRIVATE-STAGED' not in a.get('/escala', query_string={'semana': code}).data
    info = supervisor_client.get(f'/api/publicacao/{code}').get_json()
    assert any(c['after'] and c['after'].get('nota') == 'PRIVATE-STAGED' for c in info['alteracoes_pendentes'])


def test_swap_rejects_staged_change_to_requested_shift(app, supervisor_client):
    code, days, tid, _ = setup_week(app)
    a, b = login(app), login(app, 'ZBB')
    publish(supervisor_client, code)
    rid = a.post('/api/trocas', json={'turno_origem_id': tid, 'para_sigla': 'ZBB'}).get_json()['troca']['id']
    cid = b.post(f'/api/trocas/{rid}/candidatar', json={}).get_json()['candidatura']['id']
    assert supervisor_client.post(f'/api/turno/move/{tid}', json={'to_dia': days[0]['label'], 'to_loja': 'gaia', 'to_horario': '10:00-14:00'}).status_code == 200
    assert supervisor_client.post(f'/api/trocas/{rid}/decidir', json={'aprovar': True, 'candidatura_id': cid}).status_code == 409


def test_swap_checks_authorised_conflicts_hidden_by_pending_removal(app, supervisor_client):
    code, days, tid, _ = setup_week(app)
    a, b = login(app), login(app, 'ZBB')
    publish(supervisor_client, code)
    rid = a.post('/api/trocas', json={'turno_origem_id': tid, 'para_sigla': 'ZBB'}).get_json()['troca']['id']
    cid = b.post(f'/api/trocas/{rid}/candidatar', json={}).get_json()['candidatura']['id']
    with app.app.app_context():
        clash = app.Turno(semana=code, dia=days[0]['label'], loja='gaia', horario='18:00-22:00', funcionario='ZBB')
        app.db.session.add(clash); app.db.session.commit()
        app.release_week(code, 'ADMIN', 'Conflito introduzido para verificar a decisão')
        app.db.session.commit(); clash_id = clash.id
    supervisor_client.delete(f'/api/turno/remove/{clash_id}')
    response = supervisor_client.post(f'/api/trocas/{rid}/decidir', json={'aprovar': True, 'candidatura_id': cid})
    assert response.status_code == 409 and response.get_json()['conflito']


def test_legacy_migration_preserves_past_current_availability_and_holds_future(app):
    with app.app.app_context():
        marker = app.db.session.get(SchemaMigration, 'schedule_publication_v1')
        app.db.session.delete(marker)
        future, days, _, _ = setup_week(app)
        active = app.get_semana_ativa()
        active_code = active.codigo
        app.db.session.add(app.Disponibilidade(semana=active_code, dia=active.day_labels[0], funcionario='FO', tipo='disponivel'))
        app.db.session.commit()
        result = app.migration_legacy_publication()
        assert '11-17mai' in result and active_code in result and future not in result
        assert app.latest_release(future) is None
        assert app.get_semana_ativa().codigo == active_code
        assert app.Disponibilidade.query.filter_by(semana=active_code, funcionario='FO').count() == 1
        assert app.migration_legacy_publication() == []
        assert app.latest_release(active_code).actor == 'MIGRATION'


def test_assignment_ids_never_recycle_a_published_or_historical_id(app, supervisor_client):
    code, days, tid, _ = setup_week(app)
    publish(supervisor_client, code)
    supervisor_client.delete(f'/api/turno/remove/{tid}')
    publish(supervisor_client, code)
    with app.app.app_context():
        new = app.Turno(semana=code, dia=days[0]['label'], loja='gaia', horario='10:00-14:00', funcionario='ZAA')
        app.db.session.add(new); app.db.session.commit()
        assert new.id > tid


def test_publication_review_page_and_employee_history_escape_free_text(app, supervisor_client):
    code, _, tid, _ = setup_week(app)
    with app.app.app_context():
        app.db.session.get(app.Turno, tid).nota = '<script id="malicious-note">alert(1)</script>'
        app.db.session.commit()
    review = supervisor_client.get(f'/escala/publicacao/{code}')
    assert review.status_code == 200
    body = review.get_data(as_text=True)
    assert 'publication-confirm' in body and 'data-version="0"' in body
    assert 'publication.css' in body and 'publication.js' in body
    assert '<script id="malicious-note">' not in body and '&lt;script id=' in body
    assert publish(supervisor_client, code).status_code == 200
    emp = login(app)
    page = emp.get(f'/escala/publicacao/{code}')
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'Versão 1' in body and 'Responsável: ADMIN' in body
    assert '<script id="malicious-note">' not in body and '&lt;script id=' in body
    assert 'publication-confirm' not in body
    assert re.search(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}', body)
