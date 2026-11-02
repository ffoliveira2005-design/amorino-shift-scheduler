"""Rest intervals, day accounting and independent recommendation alternatives."""
import json
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client
from helpers.suggestions import build_suggestions


MONDAY = date(2027, 9, 6)
DAY = 'Seg 6'
DAYS = ['Seg 6', 'Ter 7', 'Qua 8', 'Qui 9', 'Sex 10', 'Sab 11', 'Dom 12']


def _worker(contract=0, closer=False):
    return SimpleNamespace(
        horas_contrato=contract, is_turnos=not bool(contract),
        can_open_alone=True, can_close_alone=closer)


def _scenario(**overrides):
    data = dict(
        loja='carmelitas', dia=DAY, weekday=0,
        horarios=['08:00-14:00', '14:00-20:00'], current_shifts=[],
        funcionarios={'A': _worker()},
        availability={'A': {DAY: {'tipo': 'disponivel'}}},
        weekly_hours={}, worked_days={}, same_day_shifts={}, deliveries={},
        prior={}, missing=1, day_labels=DAYS, day_date=MONDAY,
        dated_assignments={}, turnos_mean=0)
    data.update(overrides)
    return data


@pytest.mark.parametrize('invalid', ['99:00-12:00', '08:99-16:30',
                                    '08:00-08:00', '08:00-16:30 <script>'])
def test_invalid_clock_bands_are_never_recommended(invalid):
    result = build_suggestions(**_scenario(horarios=[invalid, '14:00-20:00']))
    assert result[0]['horario'] == '14:00-20:00'


def test_duplicate_overlapping_rows_do_not_inflate_coverage():
    data = _scenario(
        funcionarios={'A': _worker(), 'B': _worker()},
        current_shifts=[('08:00-14:00', 'B')],
        same_day_shifts={('B', DAY): [('carmelitas', '08:00-14:00')]},
    )
    before = build_suggestions(**data)
    data['current_shifts'] *= 3
    assert build_suggestions(**data) == before


def test_history_does_not_hide_missing_availability():
    data = _scenario(
        availability={}, prior={'n_weeks': 2, 'affinity': {
            ('A', 'carmelitas', 0, 'manha'): 1,
            ('A', 'carmelitas', 0, 'tarde'): 1}},
    )
    result = build_suggestions(**data)[0]
    assert 'Sem disponibilidade confirmada para este dia' in result['warnings']


def test_sunday_midnight_close_prefers_later_monday_band_and_reports_exact_gap():
    data = _scenario(dated_assignments={
        'A': [(MONDAY.toordinal() - 1, 23 * 60, 25 * 60)]})
    candidate = build_suggestions(**data)[0]
    assert candidate['horario'] == '14:00-20:00'
    assert not any('Intervalo' in warning for warning in candidate['warnings'])

    # With only the morning option, show the real seven-hour gap, not a generic
    # late-close warning and not a negative gap from naive midnight arithmetic.
    data['horarios'] = ['08:00-14:00']
    early = build_suggestions(**data)[0]
    assert early['warnings'] == ['Intervalo entre turnos: 7 h']


def test_already_booked_next_day_opening_downranks_late_close_with_exact_gap():
    data = _scenario(
        horarios=['08:00-16:30', '16:00-00:30'],
        dated_assignments={'A': [(MONDAY.toordinal() + 1, 8 * 60, 16 * 60)]})
    candidate = build_suggestions(**data)[0]
    assert candidate['horario'] == '08:00-16:30'
    assert not any('Intervalo' in warning for warning in candidate['warnings'])
    data['horarios'] = ['16:00-00:30']
    late = build_suggestions(**data)[0]
    assert 'Intervalo entre turnos: 7,5 h' in late['warnings']


@pytest.mark.parametrize(('band', 'neighbor'), [
    ('08:00-14:00', (-1, 23 * 60, 33 * 60)),  # Sunday duty lasts until Monday 09h
    ('19:00-08:00', (1, 6 * 60, 14 * 60)),   # Monday close overlaps Tuesday 06h
])
def test_actual_cross_date_overlap_excludes_the_only_candidate(band, neighbor):
    offset, start, end = neighbor
    data = _scenario(
        horarios=[band],
        dated_assignments={'A': [(MONDAY.toordinal() + offset, start, end)]})
    assert build_suggestions(**data) == []


def test_descarga_and_new_regular_shift_count_as_one_working_day():
    data = _scenario(
        horarios=['12:00-18:00'], weekly_hours={'A': 4},
        worked_days={'A': {DAY}}, deliveries={('A', DAY): ['08:00-12:00']},
        dated_assignments={'A': [(MONDAY.toordinal(), 8 * 60, 12 * 60)]})
    candidate = build_suggestions(**data)[0]
    assert candidate['working_days'] == candidate['projected_days'] == 1
    assert candidate['weekly_hours'] == 4
    assert candidate['shift_hours'] == 6
    assert candidate['projected_hours'] == 10
    assert not any('Intervalo' in warning for warning in candidate['warnings'])


def test_current_week_special_work_day_keeps_consecutive_warning_and_ranking_penalty():
    saturday = MONDAY + timedelta(days=5)
    data = _scenario(
        dia=DAYS[5], weekday=5, day_date=saturday, horarios=['08:00-14:00'],
        funcionarios={'A': _worker(40), 'B': _worker(40)},
        availability={sigla: {DAYS[5]: {'tipo': 'disponivel'}} for sigla in ('A', 'B')},
        weekly_hours={'A': 24, 'B': 24},
        # A worked Provas Práticas on Wednesday: it has no clock interval, but
        # Saturday would still become the sixth consecutive working day.
        worked_days={'A': set(DAYS[:5]), 'B': {DAYS[1], DAYS[3], DAYS[6]}},
        dated_assignments={
            'A': [(MONDAY.toordinal() + offset, 480, 840) for offset in (0, 1, 3, 4)],
            'B': [(MONDAY.toordinal() + offset, 480, 990) for offset in (1, 3, 6)]},
        prior={'n_weeks': 2, 'affinity': {('A', 'carmelitas', 5, 'manha'): 1}},
    )
    candidates = build_suggestions(**data)
    assert candidates[0]['sigla'] == 'B'
    special_worker = next(candidate for candidate in candidates if candidate['sigla'] == 'A')
    assert special_worker['working_days'] == 5 and special_worker['projected_days'] == 6
    assert '6 dias de trabalho consecutivos' in special_worker['warnings']


def test_one_seat_alternatives_keep_the_same_times_without_phantom_assignments():
    people = {sigla: _worker() for sigla in ('C', 'B', 'A')}
    availability = {sigla: {DAY: {'tipo': 'disponivel'}} for sigla in people}
    data = _scenario(funcionarios=people, availability=availability)
    together = build_suggestions(**data)
    assert [candidate['sigla'] for candidate in together] == ['A', 'B', 'C']
    assert len(together) == 3
    for candidate in together:
        alone = build_suggestions(**_scenario(
            funcionarios={candidate['sigla']: people[candidate['sigla']]},
            availability={candidate['sigla']: availability[candidate['sigla']]}))[0]
        assert candidate['horario'] == alone['horario'] == '08:00-14:00'
        assert candidate['explanations'] == alone['explanations']
    # Insertion order must not affect the proposed worker or alternative times.
    data['funcionarios'] = dict(reversed(list(people.items())))
    assert build_suggestions(**data) == together


def test_preference_tier_survives_overtime_and_history_secondary_scores():
    data = _scenario(
        horarios=['08:00-14:00'],
        funcionarios={'PREF': _worker(8), 'AVAILABLE': _worker(40)},
        weekly_hours={'PREF': 40, 'AVAILABLE': 0},
        availability={
            'PREF': {DAY: {'tipo': 'preferencia', 'lojas': {'carmelitas'}}},
            'AVAILABLE': {DAY: {'tipo': 'disponivel'}}},
        prior={'n_weeks': 2, 'affinity': {
            ('AVAILABLE', 'carmelitas', 0, 'manha'): 1.0}})
    candidates = build_suggestions(**data)
    assert candidates[0]['sigla'] == 'PREF'
    assert candidates[0]['reason'] == 'prefere'
    assert candidates[0]['explanations'][0] == 'Prefere esta loja'
    assert any('contrato' in warning for warning in candidates[0]['warnings'])


def test_primary_reason_uses_history_of_the_actual_selected_time():
    data = _scenario(
        horarios=['08:00-14:00', '16:00-22:00'],
        availability={'A': {DAY: {'tipo': 'disponivel', 'slots': {'noite'}}}},
        prior={'n_weeks': 2, 'affinity': {('A', 'carmelitas', 0, 'manha'): 1.0}})
    candidate = build_suggestions(**data)[0]
    assert candidate['horario'] == '16:00-22:00'
    assert candidate['reason'] == 'disponível'
    assert candidate['explanations'][0] == 'Disponibilidade confirmada'
    assert not any('Costuma' in text or 'responsável' in text
                   for text in candidate['explanations'])


def test_api_uses_previous_week_sunday_duty_for_monday_recommendations(app, supervisor_client):
    from config import HORARIOS_BASE

    with app.app.app_context():
        for code, monday in (
                ('rest-current', MONDAY),
                ('rest-previous', MONDAY - timedelta(weeks=1))):
            app.db.session.add(app.SemanaConfig(
                codigo=code, label=code, ativa=False,
                dias_json=json.dumps(app.week_from_monday(monday))))
        for person in app.Funcionario.query.all():
            app.db.session.add(app.Disponibilidade(
                semana='rest-current', dia=DAY, funcionario=person.sigla,
                tipo='indisponivel'))
        app.db.session.add(app.Funcionario(
            sigla='ZREST', nome='Teste de descanso', horas_contrato=32,
            tipo_contrato='contrato', rank='staff'))
        app.db.session.add(app.Disponibilidade(
            semana='rest-current', dia=DAY, funcionario='ZREST',
            tipo='preferencia', lojas_pref='carmelitas'))
        app.db.session.add(app.Turno(
            semana='rest-previous', dia='Dom 5', loja='carmelitas',
            funcionario='ZREST', horario='23:00-01:00'))
        for horario in HORARIOS_BASE['carmelitas']:
            app.db.session.add(app.HorarioSupressed(loja='carmelitas', horario=horario))
        for index, horario in enumerate(('08:00-14:00', '14:00-20:00')):
            app.db.session.add(app.HorarioBloco(
                loja='carmelitas', horario=horario, ordem=index))
        app.LojaHeadcountTarget.query.filter_by(loja='carmelitas', dia_idx=0).delete()
        app.db.session.add(app.LojaHeadcountTarget(loja='carmelitas', dia_idx=0, target=1))
        app.db.session.commit()

    response = supervisor_client.get(f'/api/empties/rest-current/{DAY}')
    assert response.status_code == 200
    store = next(item for item in response.get_json()['lojas'] if item['loja'] == 'carmelitas')
    assert len(store['suggestions']) == 1
    candidate = store['suggestions'][0]
    assert candidate['sigla'] == 'ZREST'
    assert candidate['horario'] == '14:00-20:00'
    assert candidate['weekly_hours'] == 0  # Sunday's hours belong to the prior week.
    assert candidate['working_days'] == 0 and candidate['projected_days'] == 1
    assert not any('Intervalo' in warning for warning in candidate['warnings'])
    assigned = supervisor_client.post('/api/turno/add', json={
        'semana': 'rest-current', 'dia': DAY, 'loja': 'carmelitas',
        'funcionario': 'ZREST', 'horario': candidate['horario'], 'nota': ''})
    assert assigned.status_code == 200, assigned.get_json()


def test_api_counts_adjacent_week_special_work_day_in_consecutive_run(app, supervisor_client):
    from config import HORARIOS_BASE

    target_day = DAYS[4]
    with app.app.app_context():
        for code, monday in (
                ('special-current', MONDAY),
                ('special-previous', MONDAY - timedelta(weeks=1))):
            app.db.session.add(app.SemanaConfig(
                codigo=code, label=code, ativa=False,
                dias_json=json.dumps(app.week_from_monday(monday))))
        for person in app.Funcionario.query.all():
            app.db.session.add(app.Disponibilidade(
                semana='special-current', dia=target_day, funcionario=person.sigla,
                tipo='indisponivel'))
        app.db.session.add(app.Funcionario(
            sigla='ZPROVAS', nome='Teste de dias consecutivos', horas_contrato=40,
            tipo_contrato='contrato', rank='staff'))
        app.db.session.add(app.Disponibilidade(
            semana='special-current', dia=target_day, funcionario='ZPROVAS',
            tipo='preferencia', lojas_pref='carmelitas'))
        app.db.session.add(app.Turno(
            semana='special-previous', dia='Dom 5', loja='carmelitas',
            funcionario='ZPROVAS', horario='Provas Práticas'))
        for day in DAYS[:4]:
            app.db.session.add(app.Turno(
                semana='special-current', dia=day, loja='carmelitas',
                funcionario='ZPROVAS', horario='08:00-14:00'))
        for horario in HORARIOS_BASE['carmelitas']:
            app.db.session.add(app.HorarioSupressed(loja='carmelitas', horario=horario))
        app.db.session.add(app.HorarioBloco(loja='carmelitas', horario='08:00-14:00', ordem=0))
        app.LojaHeadcountTarget.query.filter_by(loja='carmelitas', dia_idx=4).delete()
        app.db.session.add(app.LojaHeadcountTarget(loja='carmelitas', dia_idx=4, target=1))
        app.db.session.commit()

    response = supervisor_client.get(f'/api/empties/special-current/{target_day}')
    assert response.status_code == 200
    store = next(item for item in response.get_json()['lojas'] if item['loja'] == 'carmelitas')
    assert len(store['suggestions']) == 1
    candidate = store['suggestions'][0]
    assert candidate['sigla'] == 'ZPROVAS'
    assert candidate['weekly_hours'] == 24
    assert candidate['working_days'] == 4 and candidate['projected_days'] == 5
    assert '6 dias de trabalho consecutivos' in candidate['warnings']
    assert not any('Intervalo' in warning for warning in candidate['warnings'])
