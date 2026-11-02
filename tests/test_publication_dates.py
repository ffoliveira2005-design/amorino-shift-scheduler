"""Published shift dates stay stable while supervisor week metadata is staged."""
from datetime import date, timedelta
import json
import os
import sys

from flask import session

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client
from test_publication import setup_week, publish
from models import PublishedTurno


def test_donation_expires_on_changed_draft_dates_without_corrupting_published_cache(app, supervisor_client):
    code, days, shift_id, _ = setup_week(app)
    assert publish(supervisor_client, code).status_code == 200
    with app.app.app_context():
        week = app.SemanaConfig.query.filter_by(codigo=code).one()
        staged_days = json.loads(week.dias_json)
        for day in staged_days:
            day['date'] = (date.fromisoformat(day['date']) - timedelta(weeks=12)).isoformat()
        week.dias_json = json.dumps(staged_days)
        request = app.TrocaPedido(
            de_sigla='ZAA', para_sigla='', turno_origem_id=shift_id,
            tipo='aberto_doacao', estado='aberto', motivo='Fictitious date stability')
        app.db.session.add(request)
        app.db.session.commit()

        with app.app.test_request_context('/trocas'):
            session.update(user_sigla='ADMIN', supervisor=True)
            draft = app.db.session.get(app.Turno, shift_id)
            published = app.db.session.get(PublishedTurno, shift_id)
            # Read draft first to ensure its cached dates cannot poison the
            # published lookup in the same supervisor request.
            assert app._turno_date_iso(draft) == staged_days[0]['date']
            assert app._turno_date_iso(published) == days[0]['date']
            assert app._expire_stale_trocas() == 1
            assert request.estado == 'expirado'
            assert 'data do turno foi alterada' in request.resposta_supervisor

        with app.app.test_request_context('/minha-escala'):
            session.update(user_sigla='ZAA', supervisor=False)
            assert app._turno_info(published)['data'] == days[0]['date']
            assert app._turno_info(published)['passado'] is False
