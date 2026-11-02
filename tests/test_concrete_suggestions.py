"""The recommended time must match conflicts and its projected workload.

The shared fixture creates its own temporary database before importing app.py.
Every scenario uses synthetic workers and regular scheduling API requests.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client


def _prepare(app, loja, workers, day_index=2):
    """Make a predictable candidate pool without changing the seed roster."""
    with app.app.app_context():
        week = app.get_semana_ativa()
        days = json.loads(week.dias_json or '[]')
        day = days[day_index]['label']
        app.Turno.query.filter_by(semana=week.codigo).delete()
        app.Descarga.query.filter_by(semana=week.codigo).delete()
        app.Disponibilidade.query.filter_by(semana=week.codigo).delete()
        for person in app.Funcionario.query.all():
            app.db.session.add(app.Disponibilidade(
                semana=week.codigo, dia=day, funcionario=person.sigla,
                tipo='indisponivel'))
        for sigla, contract, slots in workers:
            app.db.session.add(app.Funcionario(
                sigla=sigla, nome='Teste ' + sigla, horas_contrato=contract,
                tipo_contrato='contrato' if contract else 'turnos', rank='staff'))
            app.db.session.add(app.Disponibilidade(
                semana=week.codigo, dia=day, funcionario=sigla,
                tipo='preferencia', lojas_pref=loja, slots_pref=slots))
        app.LojaHeadcountTarget.query.filter_by(loja=loja, dia_idx=day_index).delete()
        app.db.session.add(app.LojaHeadcountTarget(loja=loja, dia_idx=day_index, target=1))
        app.db.session.commit()
        return week.codigo, [d['label'] for d in days], day


def _suggestions(supervisor_client, week, day, loja):
    response = supervisor_client.get(f'/api/empties/{week}/{day}')
    assert response.status_code == 200
    return next(store for store in response.get_json()['lojas']
                if store['loja'] == loja)['suggestions']


def _assign(supervisor_client, week, day, loja, suggestion):
    response = supervisor_client.post('/api/turno/add', json={
        'semana': week, 'dia': day, 'loja': loja,
        'funcionario': suggestion['sigla'], 'horario': suggestion['horario'],
        'nota': '',
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['ok'] is True


def test_mbs_descarga_allows_afternoon_start_and_counts_the_day_once(app, supervisor_client):
    week, days, day = _prepare(app, 'mbs', [('ZCT', 32, 'tarde')])
    with app.app.app_context():
        app.db.session.add(app.Descarga(
            semana=week, dia=day, loja='mbs', funcionario='ZCT', horario='10:00-14:00'))
        app.db.session.commit()

    suggestions = _suggestions(supervisor_client, week, day, 'mbs')
    candidate = next(s for s in suggestions if s['sigla'] == 'ZCT')
    assert candidate['horario'].startswith('14:00-')
    assert candidate['weekly_hours'] == 4
    assert candidate['working_days'] == candidate['projected_days'] == 1
    assert candidate['projected_hours'] == 4 + candidate['shift_hours']
    _assign(supervisor_client, week, day, 'mbs', candidate)


def test_custom_descarga_window_excludes_overlapping_morning_bands(app, supervisor_client):
    week, days, day = _prepare(app, 'carmelitas', [('ZCM', 32, 'manha')])
    with app.app.app_context():
        app.db.session.add(app.Descarga(
            semana=week, dia=day, loja='carmelitas', funcionario='ZCM', horario='13:00-16:00'))
        # One morning block ends exactly at the descarga start, so an exact
        # interval check must retain it while dropping 08:00-16:30 etc.
        app.db.session.add(app.HorarioBloco(
            loja='carmelitas', horario='08:00-13:00', ordem=100))
        app.db.session.commit()

    candidate = next(s for s in _suggestions(supervisor_client, week, day, 'carmelitas')
                     if s['sigla'] == 'ZCM')
    assert candidate['horario'] == '08:00-13:00'
    assert candidate['weekly_hours'] == 4  # A short descarga still credits 4h.
    assert candidate['shift_hours'] == 5
    assert candidate['projected_hours'] == 9
    _assign(supervisor_client, week, day, 'carmelitas', candidate)


def test_no_bookable_band_omits_candidate_and_keeps_other_suggestions(app, supervisor_client):
    week, days, day = _prepare(app, 'mbs', [
        ('ZCN', 32, 'manha'), ('ZCN2', 32, 'manha'), ('ZCN3', 32, 'manha'),
        ('ZCF', 32, 'tarde'),
    ])
    from config import HORARIOS_BASE
    from helpers.time_helpers import horario_to_slot
    with app.app.app_context():
        for band in HORARIOS_BASE['mbs']:
            if horario_to_slot(band) == 'manha':
                app.db.session.add(app.HorarioSupressed(loja='mbs', horario=band))
        app.db.session.commit()

    suggestions = _suggestions(supervisor_client, week, day, 'mbs')
    assert {s['sigla'] for s in suggestions} == {'ZCF'}
    assert all(s['horario'] for s in suggestions)
    _assign(supervisor_client, week, day, 'mbs', suggestions[0])


def test_workload_projection_uses_the_selected_paid_band_and_warns_over_contract(app, supervisor_client):
    week, days, day = _prepare(app, 'carmelitas', [('ZCO', 16, 'noite')])
    from config import HORARIOS_BASE
    with app.app.app_context():
        # Keep one actual bookable band: overnight + paid extra + lunch.
        chosen = '16:00-01:30 (+1)'
        answer = app.Disponibilidade.query.filter_by(
            semana=week, dia=day, funcionario='ZCO').one()
        answer.tipo = 'disponivel'
        answer.slots_pref = ''
        for band in HORARIOS_BASE['carmelitas']:
            if band != chosen:
                app.db.session.add(app.HorarioSupressed(loja='carmelitas', horario=band))
        # An existing opener establishes the day's actual opening window;
        # only closing responsibility remains uncovered for this proposal.
        app.db.session.add(app.Turno(
            semana=week, dia=day, loja='carmelitas', funcionario='GS', horario='08:00-16:30'))
        app.LojaHeadcountTarget.query.filter_by(loja='carmelitas', dia_idx=2).one().target = 2
        app.db.session.add(app.Turno(
            semana=week, dia=days[0], loja='gaia', funcionario='ZCO', horario='14:00-20:00'))
        app.db.session.add(app.Turno(
            semana=week, dia=days[1], loja='gaia', funcionario='ZCO', horario='14:00-20:00'))
        app.db.session.commit()

    candidate = next(s for s in _suggestions(supervisor_client, week, day, 'carmelitas')
                     if s['sigla'] == 'ZCO')
    assert candidate['horario'] == chosen
    assert candidate['weekly_hours'] == 12
    assert candidate['shift_hours'] == 10
    assert candidate['projected_hours'] == 22
    assert candidate['contract_hours'] == 16
    assert candidate['working_days'] == 2 and candidate['projected_days'] == 3
    assert candidate['explanations'] and candidate['warnings']
    assert any('contrato' in warning.lower() for warning in candidate['warnings'])
    # A staff-level worker on a night band cannot fill closing responsibility.
    assert candidate['reason'] != 'responsável'
    _assign(supervisor_client, week, day, 'carmelitas', candidate)
