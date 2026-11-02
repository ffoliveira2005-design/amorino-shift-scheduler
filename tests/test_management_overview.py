"""Management figures must reconcile and remain scoped to the selected week."""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client
from helpers.management import build_management_overview


def _week(mod, offset=1):
    monday = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=offset)
    code = mod.week_codigo(monday)
    existing = mod.SemanaConfig.query.filter_by(codigo=code).first()
    if existing:
        return existing, json.loads(existing.dias_json)
    days = mod.week_from_monday(monday)
    week = mod.SemanaConfig(codigo=code, label=mod.week_label(monday),
                            dias_json=json.dumps(days), ativa=False)
    mod.db.session.add(week)
    mod.db.session.commit()
    return week, days


def test_management_surplus_never_masks_another_stores_gap(app):
    with app.app.app_context():
        week, days = _week(app)
        app.LojaHeadcountTarget.query.delete()
        for loja in ('carmelitas', 'gaia'):
            app.db.session.add(app.LojaHeadcountTarget(loja=loja, dia_idx=0, target=2))
        for person in app.Funcionario.query.limit(3).all():
            app.db.session.add(app.Turno(semana=week.codigo, dia=days[0]['label'],
                                         loja='carmelitas', horario='08:00-16:30',
                                         funcionario=person.sigla))
        app.db.session.commit()
        data = build_management_overview(week)
        assert data['covered'] == 2
        assert data['required'] == 4
        assert data['coverage_pct'] == 50
        assert data['missing'] == 2
        assert data['configured_cells'] == 2
        assert data['total_cells'] == 35


def test_management_counts_distinct_people_and_delivery_hours(app):
    with app.app.app_context():
        week, days = _week(app)
        person = app.Funcionario.query.first()
        app.db.session.add_all([
            app.Turno(semana=week.codigo, dia=days[0]['label'], loja='gaia',
                      horario='12:00-16:00', funcionario=person.sigla),
            app.Turno(semana=week.codigo, dia=days[0]['label'], loja='gaia',
                      horario='16:00-20:00', funcionario=person.sigla),
            app.Descarga(semana=week.codigo, dia=days[0]['label'], loja='gaia',
                         horario='08:00-12:00', funcionario=person.sigla),
        ])
        app.db.session.commit()
        data = build_management_overview(week)
        store = next(s for s in data['stores'] if s['id'] == 'gaia')
        assert store['cells'][0]['assigned'] == 1
        assert store['hours'] == 12.0
        assert data['total_hours'] == 12.0
        assert data['delivery_hours'] == 4.0
        assert data['scheduled_people'] == 1


def test_management_responses_and_swaps_belong_to_selected_week(app):
    with app.app.app_context():
        week, days = _week(app)
        other, other_days = _week(app, 2)
        staff = app.Funcionario.query.limit(2).all()
        for selected, selected_days, employee in ((week, days, staff[0]), (other, other_days, staff[1])):
            shift = app.Turno(semana=selected.codigo, dia=selected_days[0]['label'],
                              loja='gaia', horario='08:00-16:30', funcionario=employee.sigla)
            app.db.session.add(shift)
            app.db.session.flush()
            app.db.session.add(app.TrocaPedido(de_sigla=employee.sigla,
                                              para_sigla=staff[0].sigla,
                                              turno_origem_id=shift.id,
                                              estado='pendente_supervisor'))
            app.db.session.add(app.Disponibilidade(semana=selected.codigo,
                                                   dia=selected_days[0]['label'],
                                                   funcionario=employee.sigla, tipo='disponivel'))
        # A second response day is still one respondent.
        app.db.session.add(app.Disponibilidade(semana=week.codigo, dia=days[1]['label'],
                                               funcionario=staff[0].sigla, tipo='indisponivel'))
        app.db.session.commit()
        data = build_management_overview(week)
        assert data['responses'] == 1
        assert data['swaps'] == 1


def test_management_renders_selected_week_and_rejects_unknown_week(app, supervisor_client):
    with app.app.app_context():
        week, _ = _week(app)
        code = week.codigo
    response = supervisor_client.get('/supervisor/visao-geral', query_string={'semana': code})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Cobertura por loja' in html
    assert f'/supervisor/exportar-horas-csv?semana={code}' in html
    assert 'Não mede presença simultânea' in html
    assert 'Demonstração</strong>' not in html
    assert supervisor_client.get('/supervisor/visao-geral?semana=unknown').status_code == 404


def test_management_empty_state_and_unconfigured_targets(app, supervisor_client):
    with app.app.app_context():
        week, _ = _week(app)
        app.LojaHeadcountTarget.query.delete()
        app.db.session.commit()
        assert build_management_overview(week)['coverage_pct'] is None
        app.SemanaConfig.query.delete()
        app.db.session.commit()
    response = supervisor_client.get('/supervisor/visao-geral')
    assert response.status_code == 200
    assert 'O planeamento começa com uma semana' in response.get_data(as_text=True)


def test_management_demo_banner_is_explicit_config_only(app, supervisor_client):
    app.app.config['AMORINO_DEMO'] = True
    response = supervisor_client.get('/supervisor/visao-geral')
    assert response.status_code == 200
    assert 'Dados fictícios' in response.get_data(as_text=True)
