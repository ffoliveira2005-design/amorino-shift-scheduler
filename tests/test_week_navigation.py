"""Schedule week links use authorised dates, never insertion order or codes."""
from datetime import date
from html.parser import HTMLParser
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client, employee_client, _CsrfClient
from models import PublishedSemana, PublishedTurno, PublishedDescarga, ScheduleRelease
from helpers.week_navigation import schedule_week_navigation


class WeekMarkup(HTMLParser):
    def __init__(self, response):
        super().__init__(convert_charrefs=True)
        self.arrows = {}
        self.selected = None
        self.range = None
        self.label = ''
        self.in_range = False
        self.feed(response.get_data(as_text=True))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        if 'schedule-week-nav' in classes:
            self.selected = attrs['data-semana']
        if 'schedule-week-arrow' in classes:
            self.arrows[attrs['aria-label']] = (tag, attrs)
        if 'schedule-week-range' in classes:
            self.range = attrs
            self.in_range = True

    def handle_endtag(self, tag):
        if tag == 'span':
            self.in_range = False

    def handle_data(self, text):
        if self.in_range:
            self.label += text


def setup_weeks(mod, published=('old', 'new')):
    with mod.app.app_context():
        for model in (mod.Turno, mod.Descarga, PublishedTurno, PublishedDescarga,
                      PublishedSemana, ScheduleRelease, mod.SemanaConfig):
            model.query.delete(synchronize_session=False)
        # Deliberately reverse both IDs and lexical week codes.
        values = [('new', 'a-latest', date(2031, 1, 6)),
                  ('mid', 'zz-cross-year', date(2030, 12, 30)),
                  ('old', 'm-earliest', date(2030, 12, 23))]
        weeks = {}
        for name, code, monday in values:
            days = mod.week_from_monday(monday)
            week = mod.SemanaConfig(codigo=code, label='Etiqueta ambígua',
                dias_json=json.dumps(days), ativa=name == 'mid')
            mod.db.session.add(week)
            mod.db.session.flush()
            if name in published:
                mod.release_week(code, 'ADMIN', 'Publicação autorizada do cenário')
            weeks[name] = code
        mod.db.session.commit()
        return weeks


def query_arrow(markup, label):
    tag, attrs = markup.arrows[label]
    assert tag == 'a'
    return parse_qs(urlsplit(attrs['href']).query)


def test_editor_neighbours_use_dates_across_years_and_do_not_mutate(app, supervisor_client):
    weeks = setup_weeks(app)
    with app.app.app_context():
        before = ([(w.codigo, w.ativa, w.dias_json) for w in app.SemanaConfig.query.all()],
                  ScheduleRelease.query.count(), app.AuditLog.query.count())
    response = supervisor_client.get('/supervisor/escala', query_string={
        'semana': weeks['mid'], 'q': 'Inês & Clara', 'tipo': 'contrato', 'loja': 'gaia'})
    assert response.status_code == 200
    markup = WeekMarkup(response)
    assert markup.selected == weeks['mid']
    assert markup.label == '30 de Dezembro de 2030 a 5 de Janeiro de 2031'
    assert markup.range['data-start'] == '2030-12-30'
    assert markup.range['data-end'] == '2031-01-05'
    previous = query_arrow(markup, 'Semana anterior')
    following = query_arrow(markup, 'Semana seguinte')
    assert previous['semana'] == [weeks['old']]
    assert following['semana'] == [weeks['new']]
    for query in (previous, following):
        assert query['q'] == ['Inês & Clara']
        assert query['tipo'] == ['contrato']
        assert query['loja'] == ['gaia']
    body = response.get_data(as_text=True)
    assert 'const SEMANA' in body and weeks['mid'] in body
    for endpoint in ('exportar-pdf', 'conflitos'):
        assert f'/supervisor/{endpoint}?semana={weeks["mid"]}' in body
    assert f'/escala/descargas?semana={weeks["mid"]}' in body
    # Following a real link and refreshing retains the same week.
    linked = markup.arrows['Semana seguinte'][1]['href']
    assert WeekMarkup(supervisor_client.get(linked)).selected == weeks['new']
    assert WeekMarkup(supervisor_client.get(linked)).selected == weeks['new']
    with app.app.app_context():
        after = ([(w.codigo, w.ativa, w.dias_json) for w in app.SemanaConfig.query.all()],
                 ScheduleRelease.query.count(), app.AuditLog.query.count())
        assert after == before


@pytest.mark.parametrize('name,disabled', [('old', 'Semana anterior'), ('new', 'Semana seguinte')])
def test_editor_boundaries_have_disabled_controls(app, supervisor_client, name, disabled):
    weeks = setup_weeks(app)
    markup = WeekMarkup(supervisor_client.get('/supervisor/escala', query_string={'semana': weeks[name]}))
    tag, attrs = markup.arrows[disabled]
    assert tag == 'button' and 'disabled' in attrs and attrs['aria-disabled'] == 'true'
    assert 'href' not in attrs
    assert '2030' in markup.label or '2031' in markup.label


def test_employee_navigation_skips_unpublished_weeks_and_supervisor_readonly_uses_visible_list(app, employee_client):
    weeks = setup_weeks(app)
    supervisor_client = _CsrfClient(app.app.test_client(), app)
    supervisor_client.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'})
    employee = WeekMarkup(employee_client.get('/escala', query_string={'semana': weeks['old']}))
    assert query_arrow(employee, 'Semana seguinte')['semana'] == [weeks['new']]
    assert weeks['mid'] not in employee_client.get('/escala', query_string={'semana': weeks['old']}).get_data(as_text=True)
    readonly = WeekMarkup(supervisor_client.get('/escala', query_string={'semana': weeks['mid']}))
    assert readonly.selected == weeks['mid']
    assert query_arrow(readonly, 'Semana anterior')['semana'] == [weeks['old']]
    assert urlsplit(readonly.arrows['Semana anterior'][1]['href']).path == '/escala'
    assert employee_client.get('/escala', query_string={'semana': weeks['mid']}).status_code == 404


@pytest.mark.parametrize('path', ['/escala', '/supervisor/escala'])
def test_explicit_invalid_week_returns_404_consistently(supervisor_client, path):
    assert supervisor_client.get(path, query_string={'semana': 'not-an-existing-week'}).status_code == 404


def test_empty_and_single_authorised_lists_have_no_navigation(app, employee_client):
    weeks = setup_weeks(app, published=('old',))
    supervisor_client = _CsrfClient(app.app.test_client(), app)
    supervisor_client.post('/login', data={'sigla': 'ADMIN', 'pin': '1234'})
    only = WeekMarkup(employee_client.get('/escala', query_string={'semana': weeks['old']}))
    assert only.selected == weeks['old']
    assert all(tag == 'button' and 'disabled' in attrs for tag, attrs in only.arrows.values())
    with app.app.app_context():
        for model in (PublishedSemana, ScheduleRelease, app.SemanaConfig):
            model.query.delete(synchronize_session=False)
        app.db.session.commit()
    for caller, path in ((employee_client, '/escala'), (supervisor_client, '/supervisor/escala')):
        response = caller.get(path)
        assert response.status_code == 200
        empty = WeekMarkup(response)
        assert empty.selected == '' and empty.label == 'Sem semanas disponíveis'
        assert all(tag == 'button' and 'disabled' in attrs for tag, attrs in empty.arrows.values())
        assert caller.get(path, query_string={'semana': 'unavailable'}).status_code == 404


def test_same_calendar_date_is_not_treated_as_previous_or_next_and_undated_is_not_ordered():
    from types import SimpleNamespace
    def week(code, monday):
        days = [{'date': monday}] if monday else []
        return SimpleNamespace(codigo=code, dias_json=json.dumps(days))
    selected = week('z', '2030-12-30')
    same_date = week('a', '2030-12-30')
    undated = week('n', '')
    navigation = schedule_week_navigation([same_date, undated, selected], selected)
    assert navigation['previous'] is None and navigation['next'] is None
    navigation = schedule_week_navigation([same_date, undated, selected], undated)
    assert navigation['previous'] is None and navigation['next'] is None
    assert navigation['label'] == 'Datas da semana indisponíveis'
