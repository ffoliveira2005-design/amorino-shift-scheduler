"""Historical suggestion evidence, always against the scratch app fixture."""
import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app
from helpers.scoring import _load_prior_weeks


@pytest.fixture
def history_app(app):
    # test_basics.app sets AMORINO_DB_PATH before importing the application and
    # asserts that its engine points at its own temporary database.
    with app.app.app_context():
        app.Turno.query.delete()
        app.Descarga.query.delete()
        app.SemanaConfig.query.delete()
        app.db.session.commit()
    return app


def _week(app_mod, code, monday):
    with app_mod.app.app_context():
        app_mod.db.session.add(app_mod.SemanaConfig(
            codigo=code, label=code,
            dias_json=json.dumps(app_mod.week_from_monday(monday)), ativa=False))
        app_mod.db.session.commit()
    return code


def _turno(app_mod, code, sigla, horario='08:00-16:30', loja='carmelitas'):
    with app_mod.app.app_context():
        week = app_mod.SemanaConfig.query.filter_by(codigo=code).first()
        day = json.loads(week.dias_json)[0]['label']
        app_mod.db.session.add(app_mod.Turno(
            semana=code, dia=day, loja=loja, horario=horario,
            funcionario=sigla))
        app_mod.db.session.commit()


def _prior(app_mod, current='current'):
    with app_mod.app.app_context():
        return _load_prior_weeks(current)


@pytest.mark.parametrize('stored_date', [None, '', 'not-a-date'])
def test_history_requires_a_valid_dated_current_week(history_app, stored_date):
    app_mod = history_app
    for n in (1, 2):
        code = _week(app_mod, f'past-{n}', date(2027, 8, 30) - timedelta(weeks=n))
        _turno(app_mod, code, 'OLD')
    if stored_date is not None:
        with app_mod.app.app_context():
            app_mod.db.session.add(app_mod.SemanaConfig(
                codigo='current', label='current', ativa=False,
                dias_json=json.dumps([{'label': 'Seg 6', 'date': stored_date}])))
            app_mod.db.session.commit()
    prior = _prior(app_mod)
    assert prior == {'n_weeks': 0, 'affinity': {}, 'pair_affinity': {},
                     'loja_day_counts': {}, 'band_typical': {}}


def test_history_excludes_current_future_and_undated_weeks(history_app):
    app_mod = history_app
    anchor = date(2027, 9, 6)
    _week(app_mod, 'current', anchor)
    for code, monday, sigla in (
            ('past-1', anchor - timedelta(weeks=1), 'OLD'),
            ('past-2', anchor - timedelta(weeks=2), 'OLD'),
            ('future', anchor + timedelta(weeks=1), 'FUTURE')):
        _week(app_mod, code, monday)
        _turno(app_mod, code, sigla)
    _turno(app_mod, 'current', 'CURRENT')
    _week(app_mod, 'undated', anchor - timedelta(weeks=3))
    _turno(app_mod, 'undated', 'UNDATED')
    with app_mod.app.app_context():
        app_mod.SemanaConfig.query.filter_by(codigo='undated').first().dias_json = json.dumps(
            [{'label': 'Seg 16', 'date': ''}])
        app_mod.db.session.commit()
    prior = _prior(app_mod)
    assert prior['n_weeks'] == 2
    assert {key[0] for key in prior['affinity']} == {'OLD'}
    assert prior['affinity'][('OLD', 'carmelitas', 0, 'manha')] == 1.0


def test_blank_drafts_and_descarga_only_weeks_do_not_displace_history(history_app):
    app_mod = history_app
    anchor = date(2027, 9, 6)
    _week(app_mod, 'current', anchor)
    # More drafts than the lookback limit must not crowd out the two real weeks.
    for n in range(1, 15):
        _week(app_mod, f'draft-{n}', anchor - timedelta(weeks=n))
    with app_mod.app.app_context():
        week = app_mod.SemanaConfig.query.filter_by(codigo='draft-1').first()
        app_mod.db.session.add(app_mod.Descarga(
            semana=week.codigo, dia=json.loads(week.dias_json)[0]['label'],
            loja='carmelitas', funcionario='DELIVERY', horario='08:00-12:00'))
        app_mod.db.session.commit()
    for n in (15, 16):
        code = _week(app_mod, f'real-{n}', anchor - timedelta(weeks=n))
        _turno(app_mod, code, 'OLD')
    prior = _prior(app_mod)
    assert prior['n_weeks'] == 2
    assert prior['affinity'][('OLD', 'carmelitas', 0, 'manha')] == 1.0
    assert prior['loja_day_counts'][('carmelitas', 0)] == [1, 1]


def test_worker_slot_affinity_counts_once_per_week_despite_split_or_duplicate_rows(history_app):
    app_mod = history_app
    anchor = date(2027, 9, 6)
    _week(app_mod, 'current', anchor)
    _week(app_mod, 'recent', anchor - timedelta(weeks=1))
    _week(app_mod, 'older', anchor - timedelta(weeks=2))
    for horario in ('08:00-16:30', '08:00-16:30', '10:00-18:30'):
        _turno(app_mod, 'recent', 'RECENT', horario)
    _turno(app_mod, 'recent', 'REGULAR')
    _turno(app_mod, 'older', 'REGULAR')
    prior = _prior(app_mod)
    assert prior['affinity'][('RECENT', 'carmelitas', 0, 'manha')] == pytest.approx(0.5)
    assert prior['affinity'][('REGULAR', 'carmelitas', 0, 'manha')] == 1.0
    assert prior['loja_day_counts'][('carmelitas', 0)] == [2, 1]


def test_band_typical_weights_recent_observations_and_distinct_workers(history_app):
    app_mod = history_app
    anchor = date(2027, 9, 6)
    _week(app_mod, 'current', anchor)
    # Deliberately insert older weeks first: database IDs must not drive recency.
    for n in (5, 4, 3, 2, 1):
        code = _week(app_mod, f'past-{n}', anchor - timedelta(weeks=n))
        _turno(app_mod, code, 'BASE', '08:00-16:30')
    _turno(app_mod, 'past-1', 'BASE', '08:00-16:30')  # duplicate is not extra staff
    _turno(app_mod, 'past-1', 'NEW', '14:00-22:30')
    _turno(app_mod, 'past-5', 'OLD-A', '13:00-19:00')
    _turno(app_mod, 'past-5', 'OLD-B', '13:00-19:00')
    _turno(app_mod, 'past-1', 'SPECIAL', 'Provas Práticas')
    prior = _prior(app_mod)
    bands = prior['band_typical']
    # Four newest weeks weigh 3 each; the fifth weighs 2, total 14.
    assert bands[('carmelitas', 0, '08:00-16:30')] == 1.0
    assert bands[('carmelitas', 0, '14:00-22:30')] == pytest.approx(3 / 14)
    assert bands[('carmelitas', 0, '13:00-19:00')] == pytest.approx(4 / 14)
    assert ('carmelitas', 0, 'Provas Práticas') not in bands


def test_pair_affinity_requires_overlap_is_store_specific_and_deduplicated(history_app):
    app_mod = history_app
    anchor = date(2027, 9, 6)
    _week(app_mod, 'current', anchor)
    _week(app_mod, 'recent', anchor - timedelta(weeks=1))
    _week(app_mod, 'older', anchor - timedelta(weeks=2))
    _turno(app_mod, 'older', 'OTHER')
    for sigla, horario, loja in (
            ('A', '08:00-12:00', 'carmelitas'),
            ('A', '08:00-12:00', 'carmelitas'),
            ('B', '12:00-16:00', 'carmelitas'),  # touches A, never overlaps
            ('C', '11:00-15:00', 'carmelitas'),
            ('C', '11:00-15:00', 'carmelitas'),
            ('D', '08:00-12:00', 'gaia'),        # other store is not a coworker
            ('A', '08:00-12:00', 'gaia'),
            ('C', '11:00-15:00', 'gaia'),
            ('E', 'Provas Práticas', 'carmelitas'),
            ('F', '23:00-01:00', 'carmelitas'),
            ('G', '23:30-02:00', 'carmelitas')):
        _turno(app_mod, 'recent', sigla, horario, loja)
    pairs = _prior(app_mod)['pair_affinity']
    assert pairs[('A', 'C', 'carmelitas', 0)] == pytest.approx(0.5)
    assert pairs[('A', 'C', 'gaia', 0)] == pytest.approx(0.5)
    assert pairs[('B', 'C', 'carmelitas', 0)] == pytest.approx(0.5)
    assert pairs[('F', 'G', 'carmelitas', 0)] == pytest.approx(0.5)
    assert ('A', 'B', 'carmelitas', 0) not in pairs
    assert ('A', 'D', 'carmelitas', 0) not in pairs
    assert not any('E' in key[:2] for key in pairs)
