"""Mini roster integration: real rendered pages and assignment API on scratch DBs."""
from datetime import date
from html.parser import HTMLParser
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client, employee_client


class ScheduleMarkup(HTMLParser):
    """Inspect real HTML nodes rather than matching IDs inside JavaScript strings."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.panel = None
        self.cards = {}
        self.selected_weeks = []
        self.tags = []
        self._card = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get('class', '').split()
        self.tags.append((tag, values))
        if values.get('id') == 'schedule-team':
            self.panel = (tag, values)
        if tag == 'nav' and 'schedule-week-nav' in classes:
            self.selected_weeks.append(values.get('data-semana'))
        if tag == 'button' and 'schedule-team-person' in classes:
            self._card = {'attrs': values, 'text': []}
            self.cards[values['data-sigla']] = self._card

    def handle_endtag(self, tag):
        if tag == 'button':
            self._card = None

    def handle_data(self, value):
        if self._card is not None:
            self._card['text'].append(value)


def _team_week(app_mod, malicious_name=None):
    """A deliberately inactive week with assigned and unassigned roster members."""
    with app_mod.app.app_context():
        active = app_mod.get_semana_ativa().codigo
        days = app_mod.week_from_monday(date(2030, 1, 7))
        week = app_mod.SemanaConfig(
            codigo='team-review-week', label='Semana de revisão da equipa',
            dias_json=json.dumps(days), ativa=False)
        fixed = app_mod.Funcionario(
            sigla='TEAM_A', nome='Clara Matos', horas_contrato=32,
            tipo_contrato='contrato', rank='staff')
        flexible = app_mod.Funcionario(
            sigla='TEAM_T', nome='Inês Monteiro', horas_contrato=0,
            tipo_contrato='turnos', rank='trainee')
        app_mod.db.session.add_all([week, fixed, flexible])
        if malicious_name:
            app_mod.db.session.add(app_mod.Funcionario(
                sigla='TEAM_XSS', nome=malicious_name, horas_contrato=24,
                tipo_contrato='contrato', rank='staff',
                pin='TEST-PIN-MUST-REMAIN-PRIVATE',
                ical_token='TEST-CALENDAR-TOKEN-MUST-REMAIN-PRIVATE'))
        app_mod.db.session.commit()
        labels = [day['label'] for day in days]
        _, horarios = app_mod.build_grid('carmelitas', week.codigo, labels)
        horario = horarios[0]
        # One roster member already has a shift; the flexible member has none
        # and has sent no availability. Both must remain available in the mini.
        app_mod.db.session.add(app_mod.Turno(
            semana=week.codigo, dia=labels[0], loja='carmelitas',
            horario=horario, funcionario='TEAM_A'))
        app_mod.db.session.commit()
        return {'codigo': week.codigo, 'day': labels[1], 'horario': horario,
                'active': active, 'labels': labels}


def test_anonymous_cannot_open_supervisor_roster_editor(client):
    response = client.get('/supervisor/escala', follow_redirects=False)
    assert response.status_code == 302
    assert '/login' in response.headers['Location']
    assert ScheduleMarkup(response.get_data(as_text=True)).panel is None


def test_employee_and_store_manager_rank_do_not_get_edit_roster(app, employee_client):
    week = _team_week(app)
    with app.app.app_context():
        employee = app.db.session.get(app.Funcionario, 'FO')
        employee.rank = 'store_manager'
        employee.loja_gerida = 'carmelitas'
        app.db.session.commit()
        before = app.Turno.query.count()
    readonly = employee_client.get('/escala?semana=' + week['codigo'])
    assert readonly.status_code == 404  # This future draft has no authorised publication.
    assert ScheduleMarkup(readonly.get_data(as_text=True)).panel is None
    denied = employee_client.get('/supervisor/escala?semana=' + week['codigo'],
                                 follow_redirects=False)
    assert denied.status_code == 302
    write = employee_client.post('/api/turno/add', json={
        'semana': week['codigo'], 'dia': week['day'], 'loja': 'carmelitas',
        'horario': week['horario'], 'funcionario': 'TEAM_T', 'nota': ''})
    assert write.status_code == 403
    assert write.get_json()['ok'] is False
    with app.app.app_context():
        assert app.Turno.query.count() == before


def test_supervisor_public_schedule_remains_a_readonly_view(app, supervisor_client):
    week = _team_week(app)
    response = supervisor_client.get('/escala?semana=' + week['codigo'])
    assert response.status_code == 200
    markup = ScheduleMarkup(response.get_data(as_text=True))
    assert markup.panel is None
    assert not any(attrs.get('draggable') == 'true'
                   for tag, attrs in markup.tags
                   if 'chip' in attrs.get('class', '').split())


def test_supervisor_mini_preserves_selected_week_and_complete_roster(app, supervisor_client):
    week = _team_week(app)
    response = supervisor_client.get('/supervisor/escala?semana=' + week['codigo'])
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    markup = ScheduleMarkup(body)
    assert markup.panel is not None and markup.panel[0] == 'aside'
    assert markup.selected_weeks == [week['codigo']]
    with app.app.app_context():
        expected = {employee.sigla for employee in app.Funcionario.query.all()}
        assert app.get_semana_ativa().codigo == week['active']
        assert app.Disponibilidade.query.filter_by(
            semana=week['codigo'], funcionario='TEAM_T').count() == 0
    assert set(markup.cards) == expected
    assert {'TEAM_A', 'TEAM_T'} <= set(markup.cards)
    assert all(card['attrs'].get('type') == 'button'
               and card['attrs'].get('draggable') == 'true'
               and card['attrs'].get('aria-pressed') == 'false'
               and 'disabled' not in card['attrs']
               for card in markup.cards.values())
    # The same selected-week variable used by the real drop handler must not
    # silently revert to the active week when an inactive week is opened.
    selected = re.search(r"const\s+SEMANA\s*=\s*(['\"])(.*?)\1\s*;", body)
    assert selected and selected.group(2) == week['codigo']


def test_mini_roster_names_are_plain_text_and_never_include_credentials(app, supervisor_client):
    payload = "Ana D'Almeida </script><img src=x onerror=alert(1)>"
    week = _team_week(app, payload)
    hostile_sigla = 'T"&<X'
    with app.app.app_context():
        app.db.session.add(app.Funcionario(
            sigla=hostile_sigla, nome='João Costa', horas_contrato=16,
            tipo_contrato='contrato', rank='staff'))
        app.db.session.commit()
    response = supervisor_client.get('/supervisor/escala?semana=' + week['codigo'])
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    markup = ScheduleMarkup(body)
    assert payload in ''.join(markup.cards['TEAM_XSS']['text'])
    assert markup.cards[hostile_sigla]['attrs']['data-sigla'] == hostile_sigla
    assert 'João Costa' in ''.join(markup.cards[hostile_sigla]['text'])
    assert not any(tag == 'img' and attrs.get('src') == 'x'
                   for tag, attrs in markup.tags)
    assert '<img src=x onerror=alert(1)>' not in body
    assert not any(payload in value
                   for _, attrs in markup.tags
                   for name, value in attrs.items()
                   if name.startswith('on') and value is not None)
    assert 'TEST-PIN-MUST-REMAIN-PRIVATE' not in body
    assert 'TEST-CALENDAR-TOKEN-MUST-REMAIN-PRIVATE' not in body


def test_mini_exact_cell_assignment_uses_selected_week_and_preserves_conflicts(app, supervisor_client):
    week = _team_week(app)
    with app.app.app_context():
        active_count = app.Turno.query.filter_by(semana=week['active']).count()
        _, other_horarios = app.build_grid('gaia', week['codigo'], week['labels'])
        overlapping = next(h for h in other_horarios
                           if app._shifts_overlap(week['horario'], h))
    payload = {'semana': week['codigo'], 'dia': week['day'],
               'loja': 'carmelitas', 'horario': week['horario'],
               'funcionario': 'TEAM_T', 'nota': ''}
    created = supervisor_client.post('/api/turno/add', json=payload)
    assert created.status_code == 200
    assert created.get_json()['tipo_contrato'] == 'turnos'
    shift_id = created.get_json()['id']
    duplicate = supervisor_client.post('/api/turno/add', json=payload)
    assert duplicate.status_code == 409
    clash = supervisor_client.post('/api/turno/add', json={
        **payload, 'loja': 'gaia', 'horario': overlapping})
    assert clash.status_code == 409
    with app.app.app_context():
        original = app.db.session.get(app.Turno, shift_id)
        assert (original.semana, original.loja, original.dia, original.horario,
                original.funcionario, original.nota) == (
            week['codigo'], 'carmelitas', week['day'], week['horario'], 'TEAM_T', '')
        assert app.Turno.query.filter_by(
            semana=week['codigo'], funcionario='TEAM_T').count() == 1
        assert app.Turno.query.filter_by(semana=week['active']).count() == active_count
