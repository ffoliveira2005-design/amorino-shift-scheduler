"""Delivery-only workdays must appear in the management rest review."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client
from test_management_overview import _week
from helpers.management import build_management_overview


def test_management_rest_review_includes_delivery_only_day(app, supervisor_client):
    with app.app.app_context():
        week, days = _week(app)
        employee = app.Funcionario.query.first()
        for day in days[:5]:
            app.db.session.add(app.Turno(semana=week.codigo, dia=day['label'],
                                         loja='gaia', horario='10:00-14:00',
                                         funcionario=employee.sigla))
        app.db.session.add(app.Descarga(semana=week.codigo, dia=days[5]['label'],
                                        loja='gaia', horario='08:00-12:00',
                                        funcionario=employee.sigla))
        app.db.session.commit()
        code, sigla = week.codigo, employee.sigla
        data = build_management_overview(week)
        assert data['rest_review'] == 1
        assert data['rest_people'][0]['sigla'] == sigla
        assert data['rest_people'][0]['days'] == 6
        assert data['total_hours'] == 24.0
    response = supervisor_client.get('/supervisor/visao-geral', query_string={'semana': code})
    assert response.status_code == 200
    assert 'Pessoas a rever' in response.get_data(as_text=True)
    assert '6 dias' in response.get_data(as_text=True)
