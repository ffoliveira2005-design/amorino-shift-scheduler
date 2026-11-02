"""Regression checks for real edits exercised during the CEO rehearsal audit."""
from datetime import date
import importlib.util
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client, employee_client, PROJECT_ROOT


def _active_days(app_mod):
    with app_mod.app.app_context():
        week = app_mod.get_semana_ativa()
        return week.codigo, [d['label'] for d in week.dias_list]


def test_clearing_a_saved_availability_day_matches_the_submitted_form(app, employee_client):
    week, days = _active_days(app)
    assert employee_client.post('/disponibilidade', data={
        'semana': week, f'tipo_{days[0]}': 'disponivel',
        f'tipo_{days[1]}': 'indisponivel',
    }).status_code == 200
    response = employee_client.post('/disponibilidade', data={
        'semana': week, f'tipo_{days[0]}': '',
        f'tipo_{days[1]}': 'preferencia',
    })
    assert response.status_code == 200
    with app.app.app_context():
        answers = app.Disponibilidade.query.filter_by(semana=week, funcionario='FO').all()
        assert [(r.dia, r.tipo) for r in answers] == [(days[1], 'preferencia')]
    # The saved recap and the next GET agree that Monday is now unanswered.
    assert 'okd-blank' in response.get_data(as_text=True)
    page = employee_client.get('/disponibilidade').get_data(as_text=True)
    preload = re.search(r'const INITIAL_BY_DAY\s*=\s*(\{.*?\});', page).group(1)
    assert days[0] not in json.loads(preload)


def test_a_blank_submission_preserves_the_existing_availability(app, employee_client):
    week, days = _active_days(app)
    employee_client.post('/disponibilidade', data={
        'semana': week, f'tipo_{days[0]}': 'disponivel'})
    response = employee_client.post('/disponibilidade', data={
        'semana': week, f'tipo_{days[0]}': ''})
    assert 'É necessário marcar pelo menos um dia.' in response.get_data(as_text=True)
    with app.app.app_context():
        assert app.Disponibilidade.query.filter_by(
            semana=week, funcionario='FO', dia=days[0]).one().tipo == 'disponivel'


def test_stale_availability_form_cannot_write_to_a_different_week(app, employee_client):
    week, days = _active_days(app)
    response = employee_client.post('/disponibilidade', data={
        'semana': 'closed-or-deleted-week', f'tipo_{days[0]}': 'disponivel'})
    assert response.status_code == 400
    with app.app.app_context():
        assert app.Disponibilidade.query.filter_by(semana=week, funcionario='FO').count() == 0


def _clean_assignment(app_mod):
    with app_mod.app.app_context():
        week = app_mod.get_semana_ativa()
        day = week.dias_list[0]['label']
        app_mod.Turno.query.filter_by(funcionario='FO', semana=week.codigo).delete()
        app_mod.db.session.add(app_mod.Turno(
            semana=week.codigo, dia=day, loja='gaia',
            funcionario='FO', horario='10:00-14:00'))
        app_mod.db.session.commit()
        return week.codigo, day


def test_store_transfer_note_never_allows_simultaneous_shifts(app, supervisor_client):
    week, day = _clean_assignment(app)
    response = supervisor_client.post('/api/turno/add', json={
        'semana': week, 'dia': day, 'loja': 'mbs', 'horario': '12:00-16:00',
        'funcionario': 'FO', 'nota': 'Troca de loja'})
    assert response.status_code == 409
    assert 'sobrep' in response.get_json()['erro']
    with app.app.app_context():
        assert app.Turno.query.filter_by(funcionario='FO', semana=week).count() == 1
    # The same transfer remains possible after the first shift finishes.
    allowed = supervisor_client.post('/api/turno/add', json={
        'semana': week, 'dia': day, 'loja': 'mbs', 'horario': '14:00-18:00',
        'funcionario': 'FO', 'nota': 'Troca de loja'})
    assert allowed.status_code == 200


def test_moving_a_transfer_into_an_overlap_preserves_the_original_shift(app, supervisor_client):
    week, day = _clean_assignment(app)
    with app.app.app_context():
        shift = app.Turno(semana=week, dia=day, loja='mbs',
                          funcionario='FO', horario='16:00-20:00', nota='Troca de loja')
        app.db.session.add(shift)
        app.db.session.commit()
        shift_id = shift.id
    blocked = supervisor_client.post(f'/api/turno/move/{shift_id}', json={
        'to_dia': day, 'to_loja': 'mbs', 'to_horario': '12:00-16:00'})
    assert blocked.status_code == 409
    with app.app.app_context():
        assert app.db.session.get(app.Turno, shift_id).horario == '16:00-20:00'


@pytest.mark.parametrize('monday', ['2030-01-08', 'not-a-date', 123, None])
def test_invalid_week_creation_preserves_the_active_week(app, supervisor_client, monday):
    original, _ = _active_days(app)
    response = supervisor_client.post('/supervisor/semana/nova', json={'monday': monday})
    assert response.status_code == 400
    with app.app.app_context():
        assert app.get_semana_ativa().codigo == original


def test_week_creation_cannot_rewrite_history_from_another_year(app, supervisor_client):
    # Both 1 Jan 2024 and 1 Jan 2029 are Mondays and have the legacy code 1-7jan.
    with app.app.app_context():
        original_active = app.get_semana_ativa().codigo
        historic = app.SemanaConfig(codigo='1-7jan', label='1 a 7 de Janeiro',
            dias_json=json.dumps(app.week_from_monday(date(2024, 1, 1))), ativa=False)
        app.db.session.add(historic)
        app.db.session.commit()
        historic_id = historic.id
    response = supervisor_client.post('/supervisor/semana/nova', json={'monday': '2029-01-01'})
    assert response.status_code == 200
    code = response.get_json()['codigo']
    assert code != '1-7jan'
    with app.app.app_context():
        assert json.loads(app.db.session.get(app.SemanaConfig, historic_id).dias_json)[0]['date'] == '2024-01-01'
        assert app.get_semana_ativa().codigo == code
        assert code != original_active
        count = app.SemanaConfig.query.count()
    # Reopening that Monday uses the newly created week, with no duplicates.
    repeated = supervisor_client.post('/supervisor/semana/nova', json={'monday': '2029-01-01'})
    assert repeated.get_json()['codigo'] == code
    with app.app.app_context():
        assert app.SemanaConfig.query.count() == count


def test_personal_schedule_lists_delivery_before_the_later_regular_shift(app, employee_client):
    week, days = _active_days(app)
    with app.app.app_context():
        app.Turno.query.filter_by(semana=week, funcionario='FO').delete()
        app.db.session.add_all([
            app.Turno(semana=week, dia=days[0], loja='gaia',
                      funcionario='FO', horario='16:00-20:00'),
            app.Descarga(semana=week, dia=days[0], loja='carmelitas',
                         funcionario='FO', horario='08:00-12:00'),
            app.Turno(semana=week, dia=days[1], loja='gaia',
                      funcionario='FO', horario='06:00-10:00'),
        ])
        app.db.session.commit()
        app.release_week(week, 'ADMIN', 'Escala autorizada para ordenação de turnos e descargas')
        app.db.session.commit()
    page = employee_client.get(f'/minha-escala?semana={week}').get_data(as_text=True)
    times = re.findall(r'<div class="shift-time">([^<]+)</div>', page)
    assert times == ['08:00-12:00', '16:00-20:00', '06:00-10:00']


def test_history_import_replace_cannot_delete_another_years_schedule(app, monkeypatch):
    with app.app.app_context():
        historic = app.SemanaConfig(codigo='1-7jan', label='1 a 7 de Janeiro',
            dias_json=json.dumps(app.week_from_monday(date(2024, 1, 1))), ativa=False)
        shift = app.Turno(semana='1-7jan', dia='Seg 1', loja='gaia',
                          horario='08:00-12:00', funcionario='FO')
        app.db.session.add_all([historic, shift])
        app.db.session.commit()
        old_shift_id = shift.id
        scratch_path = app.db.engine.url.database
    # Force the importer to use this already isolated test app, without opening
    # the production app module or its default database.
    monkeypatch.setitem(sys.modules, 'app', app)
    spec = importlib.util.spec_from_file_location('readiness_history_import',
        os.path.join(PROJECT_ROOT, 'scripts', 'import_history.py'))
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    parsed = {'week_start': date(2029, 1, 1),
              'turnos': [('gaia', '12:00-16:00', 'Seg 1', 'FO')]}
    result = importer.import_to_db(parsed, scratch_path, replace=True)
    assert result['week_codigo'] == '1-7jan-2029'
    assert result['deleted'] == 0 and result['created'] == 1
    with app.app.app_context():
        assert app.db.session.get(app.Turno, old_shift_id).horario == '08:00-12:00'
        assert app.Turno.query.filter_by(semana='1-7jan-2029').count() == 1


@pytest.mark.parametrize('second_window,expected_issue', [
    ('12:00-16:00', True), ('14:00-18:00', False)])
def test_conflict_review_distinguishes_overlaps_from_marked_sequential_transfers(
        app, supervisor_client, monkeypatch, second_window, expected_issue):
    week, day = _clean_assignment(app)
    with app.app.app_context():
        app.db.session.add(app.Turno(semana=week, dia=day, loja='mbs',
            funcionario='FO', horario=second_window, nota='Troca de loja'))
        app.db.session.commit()
    captured = {}
    original = app.render_template

    def inspect_context(template, **context):
        if template == 'supervisor_conflitos.html':
            captured.update(context)
        return original(template, **context)

    monkeypatch.setattr(app, 'render_template', inspect_context)
    assert supervisor_client.get(f'/supervisor/conflitos?semana={week}').status_code == 200
    rows = [i for i in captured['issues']['double_day'] if i['sigla'] == 'FO' and i['dia'] == day]
    assert bool(rows) == expected_issue
    if expected_issue:
        assert rows[0]['overlap'] is True
