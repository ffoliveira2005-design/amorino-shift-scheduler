"""Store responsibilities persist without changing employee permissions or contracts."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client, employee_client


def _make_manager(app_mod, *, sigla='MSX', rank='store_manager', loja='gaia'):
    with app_mod.app.app_context():
        app_mod.db.session.add(app_mod.Funcionario(
            sigla=sigla, nome="Ana D'Almeida", horas_contrato=32,
            tipo_contrato='contrato', pin='existing-private-pin-hash',
            rank=rank, loja_gerida=loja, ical_token='private-calendar-token',
        ))
        app_mod.db.session.commit()
    return sigla


def _snapshot(app_mod, sigla):
    with app_mod.app.app_context():
        row = app_mod.db.session.get(app_mod.Funcionario, sigla)
        return {column.name: getattr(row, column.name)
                for column in app_mod.Funcionario.__table__.columns} if row else None


def _audit_count(app_mod):
    with app_mod.app.app_context():
        return app_mod.AuditLog.query.count()


def test_assign_change_and_clear_manager_store_preserves_other_fields(app, supervisor_client):
    sigla = _make_manager(app, rank='staff', loja='')
    original = _snapshot(app, sigla)
    for store, expected in [('carmelitas', 'carmelitas'), ('norte', 'norte'),
                            (' clerigos ', 'clerigos'), ('', '')]:
        response = supervisor_client.patch('/api/funcionario/update-rank', json={
            'sigla': sigla, 'rank': 'store_manager', 'loja_gerida': store,
        })
        assert response.status_code == 200
        assert response.get_json()['loja_gerida'] == expected
        saved = _snapshot(app, sigla)
        assert saved == {**original, 'rank': 'store_manager', 'loja_gerida': expected}


def test_omitted_manager_store_keeps_existing_assignment(app, supervisor_client):
    sigla = _make_manager(app, loja='mbs')
    original = _snapshot(app, sigla)
    response = supervisor_client.patch('/api/funcionario/update-rank', json={
        'sigla': sigla, 'rank': 'store_manager',
    })
    assert response.status_code == 200
    assert response.get_json()['loja_gerida'] == 'mbs'
    assert _snapshot(app, sigla) == original


@pytest.mark.parametrize('rank', ['staff', 'shift_supervisor', 'trainee'])
def test_changing_manager_to_other_role_clears_store(app, supervisor_client, rank):
    sigla = _make_manager(app)
    original = _snapshot(app, sigla)
    response = supervisor_client.patch('/api/funcionario/update-rank', json={
        'sigla': sigla, 'rank': rank,
    })
    assert response.status_code == 200
    assert response.get_json()['loja_gerida'] == ''
    assert _snapshot(app, sigla) == {**original, 'rank': rank, 'loja_gerida': ''}


@pytest.mark.parametrize('store', ['unknown', '<script>alert(1)</script>', None, 4, False, [], {}])
def test_invalid_store_does_not_promote_or_modify_employee(app, supervisor_client, store):
    sigla = _make_manager(app, rank='staff', loja='')
    original = _snapshot(app, sigla)
    audit_before = _audit_count(app)
    response = supervisor_client.patch('/api/funcionario/update-rank', json={
        'sigla': sigla, 'rank': 'store_manager', 'loja_gerida': store,
    })
    assert response.status_code == 400
    assert response.get_json()['ok'] is False
    assert _snapshot(app, sigla) == original
    assert _audit_count(app) == audit_before


def test_nonmanager_assignment_rejects_role_and_store_together(app, supervisor_client):
    sigla = _make_manager(app, loja='norte')
    original = _snapshot(app, sigla)
    response = supervisor_client.patch('/api/funcionario/update-rank', json={
        'sigla': sigla, 'rank': 'staff', 'loja_gerida': 'gaia',
    })
    assert response.status_code == 400
    assert _snapshot(app, sigla) == original


def test_invalid_rank_does_not_change_existing_manager_store(app, supervisor_client):
    sigla = _make_manager(app)
    original = _snapshot(app, sigla)
    response = supervisor_client.patch('/api/funcionario/update-rank', json={
        'sigla': sigla, 'rank': 'owner', 'loja_gerida': 'mbs',
    })
    assert response.status_code == 400
    assert _snapshot(app, sigla) == original


@pytest.mark.parametrize('extra,expected', [({}, ''), ({'loja_gerida': 'mbs'}, 'mbs'),
                                          ({'loja_gerida': ''}, '')])
def test_new_manager_accepts_optional_store(app, supervisor_client, extra, expected):
    response = supervisor_client.post('/api/funcionario/add', json={
        'sigla': 'NEWMS', 'nome': 'Responsável novo', 'horas': 24,
        'tipo_contrato': 'contrato', 'rank': 'store_manager', **extra,
    })
    assert response.status_code == 200
    assert response.get_json()['loja_gerida'] == expected
    saved = _snapshot(app, 'NEWMS')
    assert saved['rank'] == 'store_manager' and saved['loja_gerida'] == expected
    assert saved['horas_contrato'] == 24 and saved['tipo_contrato'] == 'contrato'
    assert saved['pin'] == ''


@pytest.mark.parametrize('rank,store', [('staff', 'gaia'), ('store_manager', 'invalid'),
                                       ('store_manager', None)])
def test_invalid_new_member_store_creates_no_employee_or_audit(app, supervisor_client, rank, store):
    audit_before = _audit_count(app)
    response = supervisor_client.post('/api/funcionario/add', json={
        'sigla': 'BADMS', 'nome': 'Não guardar', 'horas': 32,
        'rank': rank, 'loja_gerida': store,
    })
    assert response.status_code == 400
    assert _snapshot(app, 'BADMS') is None
    assert _audit_count(app) == audit_before


@pytest.mark.parametrize('operation', ['update', 'add'])
def test_employee_manager_role_does_not_grant_store_edit_permission(app, employee_client, operation):
    # Even a real store manager remains an employee session in the app.
    with app.app.app_context():
        staff = app.db.session.get(app.Funcionario, 'FO')
        staff.rank = 'store_manager'
        staff.loja_gerida = 'gaia'
        app.db.session.commit()
    original = _snapshot(app, 'FO')
    if operation == 'update':
        response = employee_client.patch('/api/funcionario/update-rank', json={
            'sigla': 'FO', 'rank': 'store_manager', 'loja_gerida': 'mbs',
        })
    else:
        response = employee_client.post('/api/funcionario/add', json={
            'sigla': 'NOAUTH', 'nome': 'Sem permissão', 'rank': 'store_manager',
            'loja_gerida': 'norte',
        })
    assert response.status_code == 403
    assert _snapshot(app, 'FO') == original
    assert _snapshot(app, 'NOAUTH') is None


@pytest.mark.parametrize('operation', ['update', 'add'])
def test_anonymous_client_cannot_assign_manager_store(app, client, operation):
    sigla = _make_manager(app)
    original = _snapshot(app, sigla)
    # Supply a valid CSRF token without a user, isolating the auth protection.
    with client.session_transaction() as session:
        session['_csrf'] = 'manager-test-csrf'
    if operation == 'update':
        response = client.patch('/api/funcionario/update-rank', json={
            'sigla': sigla, 'rank': 'store_manager', 'loja_gerida': 'mbs',
        })
    else:
        response = client.post('/api/funcionario/add', json={
            'sigla': 'NOAUTH', 'nome': 'Sem sessão', 'rank': 'store_manager',
            'loja_gerida': 'norte',
        })
    assert response.status_code == 401
    assert response.get_json()['ok'] is False
    assert _snapshot(app, sigla) == original
    assert _snapshot(app, 'NOAUTH') is None
