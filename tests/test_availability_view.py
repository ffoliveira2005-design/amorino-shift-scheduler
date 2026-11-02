"""Reviewing one response keeps the supervisor's bounded internal view state."""
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client


def _review_rows(app_mod):
    with app_mod.app.app_context():
        week = 'review-week'
        app_mod.db.session.add(app_mod.SemanaConfig(
            codigo=week, label='Semana de revisão', ativa=False,
            dias_json=json.dumps([{'label': 'Seg 5', 'date': '2026-10-05',
                                   'feriado': False, 'feriado_nome': ''}]),
        ))
        first = app_mod.Disponibilidade(
            semana=week, funcionario='FO', dia='Seg 5', tipo='disponivel',
            texto='Consulta médica', revisto=False,
        )
        second = app_mod.Disponibilidade(
            semana=week, funcionario='JA', dia='Seg 5', tipo='preferencia',
            revisto=False,
        )
        other_week = app_mod.Disponibilidade(
            semana='11-17mai', funcionario='LC', dia='Seg 11', tipo='disponivel',
            revisto=False,
        )
        app_mod.db.session.add_all([first, second, other_week])
        app_mod.db.session.commit()
        return first.id, second.id, week


def _view(response):
    assert response.status_code == 302
    target = urlsplit(response.headers['Location'])
    assert target.scheme == '' and target.netloc == ''
    assert target.path == '/supervisor/disponibilidades'
    return parse_qs(target.query, keep_blank_values=True)


def _reviewed(app_mod, row_id):
    with app_mod.app.app_context():
        return app_mod.db.session.get(app_mod.Disponibilidade, row_id).revisto


@pytest.mark.parametrize('mode', ['pending', 'notes', 'delivery'])
def test_review_preserves_form_view_with_real_absolute_referer(app, supervisor_client, mode):
    first, second, week = _review_rows(app)
    response = supervisor_client.post(f'/supervisor/marcar-revisto/{first}', data={
        'view_state': '1', 'pessoa': "Ana D'Almeida & João", 'ver': mode,
        'abrir': 'JA,FO', 'semana': 'wrong-week',
    }, headers={'Referer': 'http://127.0.0.1:5000/supervisor/disponibilidades?semana=wrong-week'})
    assert _view(response) == {
        'semana': [week], 'pessoa': ["Ana D'Almeida & João"],
        'ver': [mode], 'abrir': ['FO,JA'],
    }
    assert _reviewed(app, first) is True
    assert _reviewed(app, second) is False


def test_expanded_people_are_unique_and_limited_to_reviewed_week(app, supervisor_client):
    first, _second, week = _review_rows(app)
    response = supervisor_client.post(f'/supervisor/marcar-revisto/{first}', data={
        'view_state': '1', 'abrir': 'FO,JA,FO,LC,UNKNOWN,https://evil.example/x',
    })
    assert _view(response) == {'semana': [week], 'abrir': ['FO,JA']}


@pytest.mark.parametrize('mode', ['all', '', 'PENDING', '../trocas',
                                'https://evil.example/notes', 'notes&next=//evil.example'])
def test_malformed_or_default_filter_normalizes_to_all(app, supervisor_client, mode):
    first, _second, week = _review_rows(app)
    response = supervisor_client.post(f'/supervisor/marcar-revisto/{first}', data={
        'view_state': '1', 'ver': mode, 'abrir': '',
    })
    view = _view(response)
    assert view['semana'] == [week]
    assert view.get('ver', ['all']) == ['all']
    assert view['abrir'] == ['']


def test_new_form_can_clear_search_filter_and_all_expanded_people(app, supervisor_client):
    first, _second, week = _review_rows(app)
    response = supervisor_client.post(f'/supervisor/marcar-revisto/{first}', data={
        'view_state': '1', 'pessoa': '', 'ver': 'all', 'abrir': '',
    }, headers={'Referer': '/supervisor/disponibilidades?pessoa=old&ver=notes&abrir=FO'})
    assert _view(response) == {'semana': [week], 'abrir': ['']}


def test_search_is_trimmed_and_bounded_without_changing_destination(app, supervisor_client):
    first, _second, week = _review_rows(app)
    person = '  ' + 'Á' * 100 + '  '
    response = supervisor_client.post(f'/supervisor/marcar-revisto/{first}', data={
        'view_state': '1', 'pessoa': person,
        'return_to': 'https://evil.example', 'next': '//evil.example',
        'semana': 'https://evil.example',
    }, headers={'Referer': 'https://evil.example/return'})
    view = _view(response)
    assert view == {'semana': [week], 'pessoa': ['Á' * 80], 'abrir': ['']}


def test_review_state_still_toggles_when_using_new_form_fields(app, supervisor_client):
    first, _second, _week = _review_rows(app)
    for expected in [True, False]:
        response = supervisor_client.post(f'/supervisor/marcar-revisto/{first}', data={
            'view_state': '1', 'ver': 'notes', 'abrir': 'FO',
        })
        _view(response)
        assert _reviewed(app, first) is expected


def test_old_relative_referrer_remains_compatible(app, supervisor_client):
    first, _second, week = _review_rows(app)
    referrer = f'/supervisor/disponibilidades?semana={week}&pessoa=FO&ver=notes&abrir=FO'
    response = supervisor_client.post(f'/supervisor/marcar-revisto/{first}', data={
        'pessoa': 'ignored-without-view-state', 'ver': 'delivery',
    }, headers={'Referer': referrer})
    assert response.status_code == 302
    assert response.headers['Location'] == referrer
    assert _reviewed(app, first) is True


def test_nonexistent_response_uses_original_safe_referrer(app, supervisor_client):
    first, _second, week = _review_rows(app)
    referrer = f'/supervisor/disponibilidades?semana={week}&abrir=FO'
    response = supervisor_client.post('/supervisor/marcar-revisto/999999', data={
        'view_state': '1', 'pessoa': 'different', 'ver': 'delivery',
    }, headers={'Referer': referrer})
    assert response.status_code == 302
    assert response.headers['Location'] == referrer
    assert _reviewed(app, first) is False


def test_nonexistent_response_rejects_external_referrer(app, supervisor_client):
    response = supervisor_client.post('/supervisor/marcar-revisto/999999', data={
        'view_state': '1', 'semana': '//evil.example', 'return_to': '//evil.example',
    }, headers={'Referer': 'https://evil.example/x'})
    assert _view(response) == {}
