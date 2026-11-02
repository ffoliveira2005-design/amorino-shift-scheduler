"""Regression tests for operational defects found in the CEO demo review."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client


def _delivery_week(app_mod):
    with app_mod.app.app_context():
        week = app_mod.get_semana_ativa()
        day = week.dias_list[0]['label']
        week.descarga_config = json.dumps({day: {
            'carmelitas': '08:00-12:00', 'mbs': '10:00-14:00',
            'gaia': '12:00-16:00',
        }})
        app_mod.Turno.query.filter_by(semana=week.codigo, funcionario='FO').delete()
        app_mod.db.session.add(app_mod.Disponibilidade(
            semana=week.codigo, dia=day, funcionario='FO',
            tipo='disponivel', descarga_opt=True))
        app_mod.db.session.commit()
        return week.codigo, day


def test_descarga_assignment_rejects_overlapping_other_store_but_allows_adjacent(app, supervisor_client):
    week, day = _delivery_week(app)

    def assign(store):
        return supervisor_client.post('/api/descarga/add', json={
            'semana': week, 'dia': day, 'loja': store, 'funcionario': 'FO'})

    assert assign('carmelitas').status_code == 200
    blocked = assign('mbs')
    assert blocked.status_code == 409
    assert 'descarga' in blocked.get_json()['erro']
    assert assign('gaia').status_code == 200
    with app.app.app_context():
        assert app.Descarga.query.filter_by(semana=week, funcionario='FO').count() == 2


def test_descarga_window_change_preserves_existing_config_on_cross_store_conflict(app, supervisor_client):
    week, day = _delivery_week(app)
    for store in ('carmelitas', 'gaia'):
        assert supervisor_client.post('/api/descarga/add', json={
            'semana': week, 'dia': day, 'loja': store,
            'funcionario': 'FO'}).status_code == 200
    blocked = supervisor_client.post('/api/descarga/horario', json={
        'semana': week, 'dia': day, 'loja': 'gaia', 'horario': '11:00-15:00'})
    assert blocked.status_code == 409
    with app.app.app_context():
        week_obj = app.SemanaConfig.query.filter_by(codigo=week).first()
        assert json.loads(week_obj.descarga_config)[day]['gaia'] == '12:00-16:00'
        assert app.Descarga.query.filter_by(
            semana=week, dia=day, loja='gaia').first().horario == '12:00-16:00'


def test_delivery_optout_does_not_hide_an_existing_assignment(app, supervisor_client):
    week, day = _delivery_week(app)
    result = supervisor_client.post('/api/descarga/add', json={
        'semana': week, 'dia': day, 'loja': 'carmelitas', 'funcionario': 'FO'})
    assert result.status_code == 200
    assignment = result.get_json()['id']
    with app.app.app_context():
        app.Disponibilidade.query.filter_by(semana=week, dia=day).update({'descarga_opt': False})
        app.db.session.commit()
    page = supervisor_client.get('/escala/descargas', query_string={'semana': week}).get_data(as_text=True)
    assert 'Ninguém marcou disponibilidade para descarga neste dia.' in page
    # An existing assignment remains visible and is excluded from the pool on removal.
    import re
    chip = re.search(r'<span class="dz-chip"[^>]*data-id="' + str(assignment) + r'"[^>]*>', page)
    assert chip and 'data-sigla="FO"' in chip.group(0)
    assert 'data-eligible="false"' in chip.group(0)
    assert f'/supervisor/escala?semana={week}' in page


@pytest.mark.parametrize('horario', ['24:00-25:00', '08:60-12:00', '08:00-08:00'])
def test_descarga_window_rejects_invalid_clock_values(app, supervisor_client, horario):
    week, day = _delivery_week(app)
    response = supervisor_client.post('/api/descarga/horario', json={
        'semana': week, 'dia': day, 'loja': 'carmelitas', 'horario': horario})
    assert response.status_code == 400
    with app.app.app_context():
        cfg = json.loads(app.SemanaConfig.query.filter_by(codigo=week).first().descarga_config)
        assert cfg[day]['carmelitas'] == '08:00-12:00'


def test_turno_add_cannot_create_an_invisible_day(app, supervisor_client):
    with app.app.app_context():
        week = app.get_semana_ativa().codigo
        before = app.Turno.query.filter_by(semana=week).count()
    response = supervisor_client.post('/api/turno/add', json={
        'semana': week, 'dia': 'Seg 99', 'loja': 'gaia',
        'horario': '12:00-16:00', 'funcionario': 'FO'})
    assert response.status_code == 400
    with app.app.app_context():
        assert app.Turno.query.filter_by(semana=week).count() == before


def test_turno_move_to_an_invalid_day_preserves_the_original_assignment(app, supervisor_client):
    with app.app.app_context():
        week = app.get_semana_ativa()
        day = week.dias_list[0]['label']
        shift = app.Turno(semana=week.codigo, dia=day, loja='gaia',
                          horario='12:00-16:00', funcionario='FO')
        app.db.session.add(shift)
        app.db.session.commit()
        shift_id = shift.id
    response = supervisor_client.post(f'/api/turno/move/{shift_id}', json={
        'to_dia': 'Seg 99', 'to_loja': 'mbs', 'to_horario': '14:00-18:00'})
    assert response.status_code == 400
    with app.app.app_context():
        shift = app.db.session.get(app.Turno, shift_id)
        assert (shift.dia, shift.loja, shift.horario) == (day, 'gaia', '12:00-16:00')


def test_employee_removal_cleans_delivery_and_closes_live_swaps_without_erasing_history(app, supervisor_client):
    week, day = _delivery_week(app)
    with app.app.app_context():
        origin = app.Turno(semana=week, dia=day, loja='gaia',
                           horario='12:00-16:00', funcionario='FO')
        colleague = app.Turno(semana=week, dia=day, loja='mbs',
                              horario='16:00-20:00', funcionario='GS')
        app.db.session.add_all([origin, colleague, app.Descarga(
            semana=week, dia=day, loja='carmelitas',
            horario='08:00-12:00', funcionario='FO')])
        app.db.session.flush()
        authored = app.TrocaPedido(de_sigla='FO', para_sigla='GS',
                                  turno_origem_id=origin.id, estado='pendente_colega')
        recipient = app.TrocaPedido(de_sigla='GS', para_sigla='FO',
                                   turno_origem_id=colleague.id, estado='pendente_supervisor')
        completed = app.TrocaPedido(de_sigla='GS', para_sigla='FO',
                                   turno_origem_id=colleague.id, estado='aprovado')
        central = app.TrocaPedido(de_sigla='GS', para_sigla='',
                                 turno_origem_id=colleague.id,
                                 tipo='aberto_doacao', estado='aberto')
        app.db.session.add_all([authored, recipient, completed, central])
        app.db.session.flush()
        candidate = app.TrocaCandidatura(troca_id=central.id, sigla='FO', status='pendente')
        app.db.session.add(candidate)
        app.db.session.commit()
        ids = (authored.id, recipient.id, completed.id, central.id, candidate.id)

    response = supervisor_client.delete('/api/funcionario/delete/FO')
    assert response.status_code == 200
    assert response.get_json()['removed']['descargas'] == 1
    assert response.get_json()['removed']['trocas'] == 2
    with app.app.app_context():
        assert app.db.session.get(app.Funcionario, 'FO') is None
        assert app.Turno.query.filter_by(funcionario='FO').count() == 0
        assert app.Disponibilidade.query.filter_by(funcionario='FO').count() == 0
        assert app.Descarga.query.filter_by(funcionario='FO').count() == 0
        states = [app.db.session.get(app.TrocaPedido, tid).estado for tid in ids[:4]]
        assert states == ['cancelado', 'cancelado', 'aprovado', 'aberto']
        assert app.db.session.get(app.TrocaCandidatura, ids[4]).status == 'retirada'
        audit = app.AuditLog.query.filter_by(action='funcionario_delete', target='FO').first()
        assert 'descargas=1' in audit.detail and 'trocas_canceladas=2' in audit.detail
    # A removed recipient can no longer be assigned a shift by approving an old request.
    blocked = supervisor_client.post(f'/api/trocas/{ids[1]}/decidir', json={'aprovar': True})
    assert blocked.status_code == 409


def test_disabling_delivery_cell_removes_hidden_assignments_and_their_hours(app, supervisor_client):
    week, day = _delivery_week(app)
    for store in ('carmelitas', 'gaia'):
        assert supervisor_client.post('/api/descarga/add', json={
            'semana': week, 'dia': day, 'loja': store,
            'funcionario': 'FO'}).status_code == 200
    with app.app.app_context():
        assert app.descarga_horas_semana(week)['FO'] == 8
    response = supervisor_client.post('/supervisor/descarga', json={
        'semana': week, 'config': {day: ['gaia']}})
    assert response.status_code == 200 and response.get_json()['removed'] == 1
    with app.app.app_context():
        assignments = app.Descarga.query.filter_by(semana=week, funcionario='FO').all()
        assert [dc.loja for dc in assignments] == ['gaia']
        assert app.descarga_horas_semana(week)['FO'] == 4
    response = supervisor_client.post('/supervisor/descarga', json={
        'semana': week, 'config': {}})
    assert response.status_code == 200 and response.get_json()['removed'] == 1
    with app.app.app_context():
        assert app.Descarga.query.filter_by(semana=week).count() == 0
        assert app.descarga_horas_semana(week).get('FO', 0) == 0


def test_individual_pdf_includes_deliveries_and_credits_the_same_hours_as_workload(app, supervisor_client, monkeypatch):
    import gerar_pdf
    week, day = _delivery_week(app)
    with app.app.app_context():
        app.db.session.add_all([
            app.Turno(semana=week, dia=day, loja='gaia',
                      horario='12:00-20:30', funcionario='FO', nota='Trazer <caixa> & colher'),
            app.Descarga(semana=week, dia=day, loja='carmelitas',
                         horario='08:00-10:00', funcionario='FO'),
        ])
        app.db.session.get(app.Funcionario, 'FO').nome = 'Pessoa <Teste> & Equipa'
        app.db.session.commit()
        app.release_week(week, 'ADMIN', 'Escala autorizada para reconciliação de PDF')
        app.db.session.commit()
    paragraphs = []
    original = gerar_pdf.Paragraph

    def observe_paragraph(text, *args, **kwargs):
        paragraphs.append(text)
        return original(text, *args, **kwargs)

    monkeypatch.setattr(gerar_pdf, 'Paragraph', observe_paragraph)
    response = supervisor_client.get(f'/supervisor/exportar-pdf-individual/FO?semana={week}')
    assert response.status_code == 200 and response.data.startswith(b'%PDF')
    assert 'Descarga' in paragraphs
    assert any('1 turno · 1 descarga · Total: <b>12,0 h</b>' in p for p in paragraphs)
    assert any('Pessoa &lt;Teste&gt; &amp; Equipa' in p for p in paragraphs)
    assert 'Trazer &lt;caixa&gt; &amp; colher' in paragraphs
    assert paragraphs.index('<b>08:00-10:00</b>') < paragraphs.index('<b>12:00-20:30</b>')
    # The same two-hour delivery is credited at the 4h floor in every surface.
    workload = supervisor_client.get(f'/api/workload/{week}').get_json()
    assert workload['FO']['horas'] == 12
