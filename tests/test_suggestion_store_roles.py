"""Historical store placement and assigned-manager recommendation safeguards."""
import json
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app
from test_suggestion_history import history_app, _week, _prior
from helpers.suggestions import build_suggestions


MONDAY = date(2027, 9, 6)
DAYS = ['Seg 6', 'Ter 7', 'Qua 8', 'Qui 9', 'Sex 10', 'Sab 11', 'Dom 12']


def _assignment(app_mod, code, sigla, loja, weekday=0, horario='08:00-14:00'):
    with app_mod.app.app_context():
        week = app_mod.SemanaConfig.query.filter_by(codigo=code).first()
        day = json.loads(week.dias_json)[weekday]['label']
        app_mod.db.session.add(app_mod.Turno(
            semana=code, dia=day, funcionario=sigla, loja=loja, horario=horario))
        app_mod.db.session.commit()


def _worker(rank='staff', managed=''):
    return SimpleNamespace(horas_contrato=40, is_turnos=False, rank=rank,
                           loja_gerida=managed, can_open_alone=True, can_close_alone=True)


def _scenario(**overrides):
    data = dict(loja='carmelitas', dia=DAYS[0], weekday=0,
                horarios=['08:00-14:00'], current_shifts=[], funcionarios={'S': _worker()},
                availability={'S': {DAYS[0]: {'tipo': 'disponivel'}}}, weekly_hours={},
                worked_days={}, same_day_shifts={}, deliveries={}, prior={}, missing=1,
                day_labels=DAYS, day_date=MONDAY, dated_assignments={})
    data.update(overrides)
    return data


def test_one_week_store_habits_deduplicate_split_shifts_and_keep_top_ties(history_app):
    _week(history_app, 'current', MONDAY)
    _week(history_app, 'past', MONDAY - timedelta(weeks=1))
    for horario in ('08:00-14:00', '08:00-14:00', '16:00-22:00'):
        _assignment(history_app, 'past', 'S', 'carmelitas', horario=horario)
    _assignment(history_app, 'past', 'S', 'gaia', weekday=3, horario='16:00-22:00')
    prior = _prior(history_app)
    assert prior['n_weeks'] == 1
    assert prior['affinity'] == {}  # Generic cell evidence still needs two weeks.
    assert prior['store_day_weights'] == {('S', 'carmelitas'): 3, ('S', 'gaia'): 3}
    assert prior['usual_stores'] == {'S': ['carmelitas', 'gaia']}


def test_store_habits_weight_recency_independently_of_weekday_and_entry_time(history_app):
    _week(history_app, 'current', MONDAY)
    for n in range(1, 6):
        code = _week(history_app, f'past-{n}', MONDAY - timedelta(weeks=n))
        _assignment(history_app, code, 'OTHER', 'norte')
    _assignment(history_app, 'past-1', 'S', 'carmelitas', weekday=4, horario='16:00-22:00')
    _assignment(history_app, 'past-5', 'S', 'gaia', weekday=0, horario='08:00-14:00')
    prior = _prior(history_app)
    assert prior['store_day_weights'][('S', 'carmelitas')] == 3
    assert prior['store_day_weights'][('S', 'gaia')] == 2
    assert prior['usual_stores']['S'] == ['carmelitas']


def test_store_habits_ignore_future_current_undated_and_blank_drafts(history_app):
    _week(history_app, 'current', MONDAY)
    _week(history_app, 'future', MONDAY + timedelta(weeks=1))
    _week(history_app, 'undated', MONDAY - timedelta(weeks=1))
    for code in ('current', 'future', 'undated'):
        for weekday in range(7):
            _assignment(history_app, code, 'S', 'carmelitas', weekday=weekday)
    with history_app.app.app_context():
        history_app.SemanaConfig.query.filter_by(codigo='undated').first().dias_json = json.dumps(
            [{'label': 'Seg 30', 'date': ''}])
        history_app.db.session.commit()
    for n in range(1, 15):
        _week(history_app, f'draft-{n}', MONDAY - timedelta(weeks=n))
    _week(history_app, 'past', MONDAY - timedelta(weeks=15))
    _assignment(history_app, 'past', 'S', 'gaia', weekday=5)
    prior = _prior(history_app)
    assert prior['n_weeks'] == 1
    assert prior['store_day_weights'] == {('S', 'gaia'): 3}
    assert prior['usual_stores'] == {'S': ['gaia']}


@pytest.mark.parametrize('prior', [{}, {'usual_stores': {'S': ['gaia']}}])
def test_supervisor_without_this_usual_store_is_not_automatically_recommended(prior):
    data = _scenario(funcionarios={'S': _worker('shift_supervisor')}, prior=prior,
                     availability={'S': {DAYS[0]: {'tipo': 'preferencia', 'lojas': {'carmelitas'}}}})
    assert build_suggestions(**data) == []


@pytest.mark.parametrize('loja', ['carmelitas', 'gaia'])
def test_supervisor_can_be_recommended_at_either_tied_usual_store_without_cell_affinity(loja):
    result = build_suggestions(**_scenario(
        loja=loja, funcionarios={'S': _worker('shift_supervisor')},
        prior={'n_weeks': 1, 'usual_stores': {'S': ['carmelitas', 'gaia']}}))[0]
    assert result['reason'] == 'histórico'
    assert result['explanations'][0] == 'Loja habitual'
    assert not any('período' in text for text in result['explanations'])


def test_assigned_manager_store_bonus_changes_choice_within_same_availability_tier():
    manager = _worker('store_manager')
    data = _scenario(
        funcionarios={'A': _worker('store_manager'), 'Z': manager},
        availability={sigla: {DAYS[0]: {'tipo': 'disponivel'}} for sigla in ('A', 'Z')},
        prior={'n_weeks': 2, 'affinity': {('A', 'carmelitas', 0, 'manha'): 1}})
    assert build_suggestions(**data)[0]['sigla'] == 'A'
    manager.loja_gerida = 'carmelitas'
    result = build_suggestions(**data)[0]
    assert result['sigla'] == 'Z'
    assert result['reason'] == 'responsável'
    assert result['explanations'][0] == 'Gere esta loja'
    assert result['shift_hours'] == result['projected_hours'] == 6
    assert result['projected_days'] == 1


def test_assigned_manager_never_overrides_higher_availability_preference_tier():
    data = _scenario(
        funcionarios={'A': _worker(), 'Z': _worker('store_manager', 'carmelitas')},
        availability={'A': {DAYS[0]: {'tipo': 'preferencia', 'lojas': {'gaia'}}},
                      'Z': {DAYS[0]: {'tipo': 'disponivel'}}})
    result = build_suggestions(**data)
    assert result[0]['sigla'] == 'A'
    assert 'Gere esta loja' in result[1]['explanations']


@pytest.mark.parametrize('restriction', ['unavailable', 'slot', 'regular', 'delivery', 'overnight'])
def test_assigned_manager_store_bonus_preserves_hard_availability_and_conflict_filters(restriction):
    data = _scenario(funcionarios={'S': _worker('store_manager', 'carmelitas')})
    if restriction == 'unavailable':
        data['availability'] = {'S': {DAYS[0]: {'tipo': 'indisponivel'}}}
    elif restriction == 'slot':
        data['availability'] = {'S': {DAYS[0]: {'tipo': 'disponivel', 'slots': {'noite'}}}}
    elif restriction == 'regular':
        data['same_day_shifts'] = {('S', DAYS[0]): [('gaia', '16:00-22:00')]}
    elif restriction == 'delivery':
        data['deliveries'] = {('S', DAYS[0]): ['08:00-12:00']}
    else:
        data['dated_assignments'] = {'S': [(MONDAY.toordinal() - 1, 23 * 60, 33 * 60)]}
    assert build_suggestions(**data) == []


def test_assigned_manager_retains_exact_short_rest_and_overtime_warnings():
    data = _scenario(funcionarios={'S': _worker('store_manager', 'carmelitas')},
                     weekly_hours={'S': 39},
                     dated_assignments={'S': [(MONDAY.toordinal() - 1, 23 * 60, 25 * 60)]})
    result = build_suggestions(**data)[0]
    assert result['projected_hours'] == 45
    assert 'Ultrapassa o contrato em 5 h' in result['warnings']
    assert 'Intervalo entre turnos: 7 h' in result['warnings']


def test_empty_manager_assignment_and_missing_rank_metadata_preserve_previous_behavior():
    manager = _worker('store_manager')
    data = _scenario(funcionarios={'S': manager})
    expected = build_suggestions(**data)
    del manager.loja_gerida
    assert build_suggestions(**data) == expected
    del manager.rank
    assert build_suggestions(**data) == expected
