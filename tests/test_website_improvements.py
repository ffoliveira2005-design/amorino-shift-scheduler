"""Tests for the July 2026 website-review fixes.

Covers:
  - mondays_from_current_week() includes the week we're living in (the
    Semanas tab must offer the current week for creation mid-week).
  - /api/upcoming-weeks starts at the current week (kept in sync with the
    dashboard's `upcoming` list, which is zipped by index).
  - build_grid() sorts rows by shift start time, so custom blocks don't pile
    up at the bottom of the store grid.
  - _expire_stale_trocas() retires central pedidos whose shift day passed,
    and /api/trocas/abrir refuses to publish a turno that already happened.
  - The supervisor today-panel early return carries every key the template
    reads (the "/5 lojas abertas" blank-number bug).
"""
import sys, os, json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client


def test_mondays_from_current_week_includes_today(app):
    mondays = app.mondays_from_current_week(8)
    assert len(mondays) == 8
    today = date.today()
    assert mondays[0] <= today <= mondays[0] + timedelta(days=6)
    assert mondays[0].weekday() == 0
    assert mondays[1] - mondays[0] == timedelta(weeks=1)


def test_upcoming_weeks_api_starts_with_current_week(app, supervisor_client):
    r = supervisor_client.get('/api/upcoming-weeks')
    assert r.status_code == 200
    weeks = r.get_json()
    first_monday = date.fromisoformat(weeks[0]['monday'])
    assert first_monday <= date.today() <= first_monday + timedelta(days=6)


def test_build_grid_sorts_rows_by_start_time(app):
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        dias = [d['label'] for d in json.loads(ativa.dias_json or '[]')]
        # A custom block that starts mid-morning: previously it was appended
        # after every base block; now it must slot in by start time.
        app.db.session.add(app.HorarioBloco(loja='carmelitas',
                                            horario='13:00-19:00', ordem=1000))
        app.db.session.commit()
        _grid, horarios = app.build_grid('carmelitas', ativa.codigo, dias)
        assert '13:00-19:00' in horarios
        starts = []
        for h in horarios:
            s, _e = app.shift_bounds(h)
            if s is not None:
                starts.append(s)
        assert starts == sorted(starts), f'rows out of order: {horarios}'


def _make_week(app_mod, monday):
    """Create a SemanaConfig for the given Monday and return it."""
    dias = app_mod.week_from_monday(monday)
    sem = app_mod.SemanaConfig(codigo=app_mod.week_codigo(monday),
                               label=app_mod.week_label(monday),
                               dias_json=json.dumps(dias), ativa=False)
    app_mod.db.session.add(sem)
    app_mod.db.session.commit()
    return sem, dias


def test_expire_stale_central_closes_past_offers(app):
    with app.app.app_context():
        today = date.today()
        past_monday = today - timedelta(days=today.weekday(), weeks=2)
        next_monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        past_sem, past_dias = _make_week(app, past_monday)
        next_sem, next_dias = _make_week(app, next_monday)

        t_past = app.Turno(semana=past_sem.codigo, dia=past_dias[0]['label'],
                           loja='carmelitas', horario='10:00-18:30',
                           funcionario='FO')
        t_next = app.Turno(semana=next_sem.codigo, dia=next_dias[0]['label'],
                           loja='carmelitas', horario='10:00-18:30',
                           funcionario='FO')
        app.db.session.add_all([t_past, t_next])
        app.db.session.commit()
        app.release_week(past_sem.codigo, 'ADMIN', 'Escala passada autorizada para expiração')
        app.release_week(next_sem.codigo, 'ADMIN', 'Escala futura autorizada para expiração')
        app.db.session.commit()

        p_past = app.TrocaPedido(de_sigla='FO', para_sigla='',
                                 turno_origem_id=t_past.id, turno_destino_id=None,
                                 estado='aberto', tipo='aberto_doacao')
        p_next = app.TrocaPedido(de_sigla='FO', para_sigla='',
                                 turno_origem_id=t_next.id, turno_destino_id=None,
                                 estado='aberto', tipo='aberto_doacao')
        app.db.session.add_all([p_past, p_next])
        app.db.session.commit()

        n = app._expire_stale_trocas()
        assert n == 1
        assert p_past.estado == 'expirado'
        assert p_next.estado == 'aberto'


def test_central_abrir_rejects_past_turno(app, client):
    with app.app.app_context():
        today = date.today()
        past_monday = today - timedelta(days=today.weekday(), weeks=2)
        past_sem, past_dias = _make_week(app, past_monday)
        t_past = app.Turno(semana=past_sem.codigo, dia=past_dias[0]['label'],
                           loja='carmelitas', horario='10:00-18:30',
                           funcionario='FO')
        app.db.session.add(t_past)
        app.db.session.commit()
        app.release_week(past_sem.codigo, 'ADMIN', 'Escala passada autorizada para teste')
        app.db.session.commit()
        tid = t_past.id
    with app.app.app_context():
        app.db.session.get(app.Funcionario, 'FO').pin = app.hash_pin('8317')
        app.db.session.commit()
    r = client.post('/login', data={'sigla': 'FO', 'pin': '8317'},
                    follow_redirects=False)
    assert r.status_code in (302, 303)
    r = client.post('/api/trocas/abrir',
                    json={'turno_origem_id': tid, 'tipo': 'aberto_doacao'})
    assert r.status_code == 400
    assert 'passou' in r.get_json()['erro']


def _login_fo(client):
    """Provision a fictional credential before testing employee workflows."""
    app_mod = client._app_mod
    with app_mod.app.app_context():
        app_mod.db.session.get(app_mod.Funcionario, 'FO').pin = app_mod.hash_pin('8317')
        app_mod.db.session.commit()
    r = client.post('/login', data={'sigla': 'FO', 'pin': '8317'},
                    follow_redirects=False)
    assert r.status_code in (302, 303)


def test_disponibilidade_answer_ahead_week(app, client):
    """Employees can submit availability for a future created week, not just
    the active one; a stale form must not write to another week."""
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        ativa_monday = date.fromisoformat(
            __import__('json').loads(ativa.dias_json)[0]['date'])
        fut_sem, fut_dias = _make_week(app, ativa_monday + timedelta(weeks=1))
        fut_codigo = fut_sem.codigo
        fut_d0 = fut_dias[0]['label']
    _login_fo(client)

    # The form for the future week renders that week (not the active one)
    r = client.get(f'/disponibilidade?semana={fut_codigo}')
    assert r.status_code == 200
    assert f'value="{fut_codigo}"' in r.data.decode('utf-8')

    # Submitting for the future week writes rows keyed to it
    r = client.post('/disponibilidade',
                    data={'semana': fut_codigo, f'tipo_{fut_d0}': 'disponivel'})
    assert r.status_code == 200
    with app.app.app_context():
        rows = app.Disponibilidade.query.filter_by(
            semana=fut_codigo, funcionario='FO').all()
        assert len(rows) == 1 and rows[0].tipo == 'disponivel'

    # A week code outside the allowed set must not modify the active week.
    with app.app.app_context():
        d0_ativa = app.get_semana_ativa().dias_list[0]['label']
    r = client.post('/disponibilidade',
                    data={'semana': 'nao-existe', f'tipo_{d0_ativa}': 'preferencia'})
    assert r.status_code == 400
    with app.app.app_context():
        ativa_cod = app.get_semana_ativa().codigo
        rows = app.Disponibilidade.query.filter_by(
            semana=ativa_cod, funcionario='FO').all()
        assert rows == []


def test_disponibilidade_success_shows_recap(app, client):
    """The success screen recaps exactly what was submitted, day by day."""
    _login_fo(client)
    with app.app.app_context():
        dias = app.get_semana_ativa().day_labels
    r = client.post('/disponibilidade', data={
        f'tipo_{dias[0]}': 'disponivel',
        f'tipo_{dias[1]}': 'indisponivel',
    })
    body = r.data.decode('utf-8')
    assert 'Resumo da submissão' in body
    assert 'ok-recap' in body
    assert 'okd-disp' in body and 'okd-indisp' in body and 'okd-blank' in body


def test_today_panel_fallback_has_all_template_keys(app):
    with app.app.app_context():
        # Force every semana into the past so no week covers today: the panel
        # must take the early-return path and still carry the stat keys.
        shift = timedelta(days=35)
        for sem in app.SemanaConfig.query.all():
            dias = json.loads(sem.dias_json or '[]')
            for d in dias:
                if d.get('date'):
                    d['date'] = (date.fromisoformat(d['date']) - shift).isoformat()
            sem.dias_json = json.dumps(dias)
        app.db.session.commit()

        panel = app._build_supervisor_today_panel()
        assert panel is not None
        assert panel['today_label'] is None
        for key in ('total_turnos', 'total_hours', 'now_people',
                    'lojas_com_pessoal', 'drafting_is_different', 'is_active'):
            assert key in panel, f'missing key: {key}'
        assert panel['lojas_com_pessoal'] == 0
