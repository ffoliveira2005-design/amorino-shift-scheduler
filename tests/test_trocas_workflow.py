"""Trocas (shift swaps), end to end — the September 2026 workflow review.

The feature shipped in May with a dozen tests that were later lost; until now
almost nothing here was covered. These pin the workflow as it now stands:

  - A request is refused at the first step that can see a problem — a shift
    already worked, a shift already promised in another open request, or a
    swap that would leave someone with two overlapping shifts — instead of
    travelling through two people and failing at the supervisor.
  - Everyone involved keeps sight of a request: the colleague who accepted,
    the candidate who was not picked or was refused.
  - A supervisor's "Recusar" closes a direct request (it is no longer
    republished to the whole team, reason included); a central post goes back
    to the central for another candidate.
  - Unanswered requests expire once their day has passed.
  - Timestamps reach the browser with their UTC offset.
"""
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, _CsrfClient, PROJECT_ROOT  # noqa: F401  (fixture)

PIN = '8317'


# ── helpers ───────────────────────────────────────────────────────────────────

def _week(app_mod, monday):
    codigo = app_mod.week_codigo(monday)
    sem = app_mod.SemanaConfig.query.filter_by(codigo=codigo).first()
    if not sem:
        sem = app_mod.SemanaConfig(codigo=codigo, label=app_mod.week_label(monday),
                                   dias_json=json.dumps(app_mod.week_from_monday(monday)),
                                   ativa=False)
        app_mod.db.session.add(sem)
        app_mod.db.session.commit()
    return sem


def _turno(app_mod, sigla, day, loja='gaia', horario='10:00-18:30'):
    sem = _week(app_mod, day - timedelta(days=day.weekday()))
    label = next(d['label'] for d in json.loads(sem.dias_json)
                 if d['date'] == day.isoformat())
    t = app_mod.Turno(semana=sem.codigo, dia=label, loja=loja, horario=horario,
                      funcionario=sigla)
    app_mod.db.session.add(t)
    app_mod.db.session.commit()
    # Swap regression fixtures represent authorised employee shifts.
    app_mod.release_week(sem.codigo, 'ADMIN', 'Escala autorizada para teste de troca')
    app_mod.db.session.commit()
    return t.id


def _people(app_mod, *siglas):
    for s in siglas:
        app_mod.db.session.add(app_mod.Funcionario(
            sigla=s, nome=f'Pessoa {s}', horas_contrato=32, tipo_contrato='contrato'))
    app_mod.db.session.commit()


def _login(app_mod, sigla, pin=PIN):
    if sigla != 'ADMIN':
        with app_mod.app.app_context():
            employee = app_mod.db.session.get(app_mod.Funcionario, sigla)
            if not employee.pin:
                employee.pin = app_mod.hash_pin(pin)
                app_mod.db.session.commit()
    c = _CsrfClient(app_mod.app.test_client(), app_mod)
    r = c.post('/login', data={'sigla': sigla, 'pin': pin})
    assert r.status_code == 302, f'login {sigla} failed: {r.status_code}'
    return c


def _estado(app_mod, tid):
    with app_mod.app.app_context():
        return app_mod.db.session.get(app_mod.TrocaPedido, tid).estado


def _owner(app_mod, turno_id):
    with app_mod.app.app_context():
        return app_mod.db.session.get(app_mod.Turno, turno_id).funcionario


SOON = date.today() + timedelta(days=2)


def _setup(app_mod):
    """ZZA and ZZB each have a shift on SOON (different stores, same hours);
    ZZC is free that day. Returns the three clients and the two shift ids."""
    with app_mod.app.app_context():
        _people(app_mod, 'ZZA', 'ZZB', 'ZZC')
        a = _turno(app_mod, 'ZZA', SOON, loja='gaia')
        b = _turno(app_mod, 'ZZB', SOON, loja='carmelitas')
    return (_login(app_mod, 'ZZA'), _login(app_mod, 'ZZB'), _login(app_mod, 'ZZC'),
            _login(app_mod, 'ADMIN', '1234'), a, b)


# ── the happy path, and who keeps seeing what ─────────────────────────────────

def test_direct_swap_happy_path_moves_both_shifts(app):
    za, zb, _, sup, a, b = _setup(app)
    r = za.post('/api/trocas', json={'turno_origem_id': a, 'para_sigla': 'ZZB',
                                     'turno_destino_id': b, 'motivo': 'Consulta'})
    assert r.status_code == 200, r.get_json()
    tid = r.get_json()['troca']['id']
    assert zb.post(f'/api/trocas/{tid}/responder', json={'aceitar': True}).get_json()['ok']
    assert sup.post(f'/api/trocas/{tid}/decidir', json={'aprovar': True}).get_json()['ok']
    assert _estado(app, tid) == 'aprovado'
    assert (_owner(app, a), _owner(app, b)) == ('ZZB', 'ZZA')


def test_colleague_keeps_seeing_a_request_they_accepted(app):
    """It used to vanish from the accepting colleague's page until the
    supervisor decided — neither "to decide" nor "sent by me" any more."""
    za, zb, _, _, a, b = _setup(app)
    tid = za.post('/api/trocas', json={'turno_origem_id': a, 'para_sigla': 'ZZB',
                                       'turno_destino_id': b}).get_json()['troca']['id']
    body = zb.get('/trocas').data.decode('utf-8')
    assert 'Resposta pendente' in body          # worded for the reader
    zb.post(f'/api/trocas/{tid}/responder', json={'aceitar': True})
    body = zb.get('/trocas').data.decode('utf-8')
    assert 'Os meus pedidos' in body
    assert 'A aguardar a supervisão' in body
    assert f'id="card-{tid}"' in body


def test_supervisor_refusal_closes_a_direct_request_without_publishing_it(app):
    """A refused direct request was turned into a PUBLIC central post — the
    author's reason included — which the author never chose to publish, and
    the colleague who had accepted just saw it disappear."""
    za, zb, _, sup, a, b = _setup(app)
    tid = za.post('/api/trocas', json={'turno_origem_id': a, 'para_sigla': 'ZZB',
                                       'turno_destino_id': b,
                                       'motivo': 'Consulta médica'}).get_json()['troca']['id']
    zb.post(f'/api/trocas/{tid}/responder', json={'aceitar': True})
    r = sup.post(f'/api/trocas/{tid}/decidir', json={'aprovar': False, 'nota': 'Formação'})
    assert r.get_json()['ok']
    assert _estado(app, tid) == 'negado'
    assert zb.get('/api/central').get_json()['pedidos'] == []
    for client in (za, zb):
        body = client.get('/trocas').data.decode('utf-8')
        assert 'Recusado pela supervisão' in body and 'Formação' in body
    # The shift is free again, and the author can try someone else from there.
    assert 'Pedir a outra pessoa' in za.get('/trocas').data.decode('utf-8')
    assert (_owner(app, a), _owner(app, b)) == ('ZZA', 'ZZB')


def test_supervisor_refusal_returns_a_genuine_exchange_and_vetoes_the_candidate(app):
    za, zb, _, sup, a, b = _setup(app)
    tid = za.post('/api/trocas/abrir', json={'turno_origem_id': a,
                                             'tipo': 'aberto_troca'}).get_json()['troca']['id']
    assert zb.post(f'/api/trocas/{tid}/candidatar', json={'turno_oferecido_id': b}).get_json()['ok']
    cid = za.get('/api/central').get_json()['pedidos'][0]['candidaturas'][0]['id']
    assert za.post(f'/api/trocas/{tid}/escolher', json={'candidatura_id': cid}).get_json()['ok']
    assert sup.post(f'/api/trocas/{tid}/decidir', json={'aprovar': False}).get_json()['ok']
    assert _estado(app, tid) == 'aberto'
    r = zb.post(f'/api/trocas/{tid}/candidatar', json={'turno_oferecido_id': b})
    assert r.status_code == 403
    assert 'recusou-te' not in r.get_json()['erro']
    body = zb.get('/trocas').data.decode('utf-8')
    assert 'A supervisão recusou esta pessoa para este pedido.' in body
    assert 'Candidatura a Pessoa ZZA' in body           # and in their history


# ── refused at the first step that can see the problem ───────────────────────

def test_request_refused_for_a_shift_already_worked(app):
    with app.app.app_context():
        _people(app, 'ZZA', 'ZZB')
        past = _turno(app, 'ZZA', date.today() - timedelta(days=1))
    za = _login(app, 'ZZA')
    for url, body in (('/api/trocas', {'turno_origem_id': past, 'para_sigla': 'ZZB'}),
                      ('/api/trocas/abrir', {'turno_origem_id': past, 'tipo': 'aberto_doacao'})):
        r = za.post(url, json=body)
        assert r.status_code == 400 and 'passou' in r.get_json()['erro'], url


def test_a_shift_can_only_be_in_one_open_request(app):
    """The same shift could go to two colleagues at once (or to one and the
    central): both could accept and the second approval hit "mudou de dono"."""
    za, _, _, _, a, _ = _setup(app)
    assert za.post('/api/trocas', json={'turno_origem_id': a, 'para_sigla': 'ZZC'}).status_code == 200
    again = za.post('/api/trocas', json={'turno_origem_id': a, 'para_sigla': 'ZZB',
                                         'turno_destino_id': None})
    central = za.post('/api/trocas/abrir', json={'turno_origem_id': a, 'tipo': 'aberto_doacao'})
    assert again.status_code == 409 and central.status_code == 409


def test_doacao_nomination_is_ignored_and_busy_candidate_is_refused(app):
    za, zb, _, _, a, _ = _setup(app)
    r = za.post('/api/trocas', json={'turno_origem_id': a, 'para_sigla': 'ZZB'})
    assert r.status_code == 200 and r.get_json()['troca']['para_sigla'] == ''
    r = zb.post(f"/api/trocas/{r.get_json()['troca']['id']}/candidatar", json={})
    assert r.status_code == 409
    assert r.get_json()['conflito'] is True
    assert 'ZZB ficaria com dois turnos sobrepostos' in r.get_json()['erro']


def test_accepting_is_refused_when_it_would_double_book(app):
    za, _, zc, sup, a, _ = _setup(app)
    with app.app.app_context():
        offered = _turno(app, 'ZZC', SOON + timedelta(days=1))
    tid = za.post('/api/trocas', json={'turno_origem_id': a,
                                       'para_sigla': 'ZZC', 'turno_destino_id': offered}).get_json()['troca']['id']
    with app.app.app_context():                 # ZZC gets busy in the meantime
        _turno(app, 'ZZC', SOON, loja='norte', horario='12:00-20:00')
    body = zc.get('/trocas').data.decode('utf-8')
    assert 'ZZC ficaria com dois turnos sobrepostos' in body   # said before the click
    r = zc.post(f'/api/trocas/{tid}/responder', json={'aceitar': True})
    assert r.status_code == 409
    assert _estado(app, tid) == 'pendente_colega'


def test_candidatura_checks_conflicts_and_promised_shifts(app):
    za, zb, zc, _, a, b = _setup(app)
    tid = za.post('/api/trocas/abrir', json={'turno_origem_id': a,
                                             'tipo': 'aberto_troca'}).get_json()['troca']['id']
    # ZZB offering their own same-day shift is a clean swap of stores.
    assert zb.post(f'/api/trocas/{tid}/candidatar',
                   json={'turno_oferecido_id': b}).get_json()['ok']
    # ...and that shift is now spoken for: it can't also be given away.
    r = zb.post('/api/trocas', json={'turno_origem_id': b, 'para_sigla': 'ZZC'})
    assert r.status_code == 409
    # ZZC offering a shift on another day would leave ZZA with the other one
    # and ZZC with ZZA's — fine; but a shift ZZA would clash with is refused.
    with app.app.app_context():
        clash = _turno(app, 'ZZC', SOON + timedelta(days=1), horario='09:00-17:00')
        _turno(app, 'ZZA', SOON + timedelta(days=1), loja='mbs', horario='10:00-14:00')
    r = zc.post(f'/api/trocas/{tid}/candidatar', json={'turno_oferecido_id': clash})
    assert r.status_code == 409 and 'ZZA ficaria com dois turnos sobrepostos' in r.get_json()['erro']


def test_unanswered_request_expires_once_its_day_has_passed(app):
    with app.app.app_context():
        _people(app, 'ZZA', 'ZZB')
        a = _turno(app, 'ZZA', SOON)
        p = app.TrocaPedido(de_sigla='ZZA', para_sigla='ZZB', turno_origem_id=a,
                            estado='pendente_colega')
        app.db.session.add(p)
        app.db.session.commit()
        tid = p.id
        # Move that week into the past, as if the day had come and gone.
        t = app.db.session.get(app.Turno, a)
        sem = app.SemanaConfig.query.filter_by(codigo=t.semana).first()
        dias = json.loads(sem.dias_json)
        for d in dias:
            d['date'] = (date.fromisoformat(d['date']) - timedelta(days=21)).isoformat()
        sem.dias_json = json.dumps(dias)
        app.db.session.commit()
        app.release_week(sem.codigo, 'ADMIN', 'Datas autorizadas do cenário de expiração')
        app.db.session.commit()
    body = _login(app, 'ZZB').get('/trocas').data.decode('utf-8')
    assert _estado(app, tid) == 'expirado'
    assert 'Por decidir' not in body


# ── what the pages and APIs say ───────────────────────────────────────────────

def test_troca_timestamps_carry_their_utc_offset(app):
    """Naive ISO strings are read by the browser as LOCAL time: in summer every
    "há X min" on /trocas was an hour late."""
    za, _, _, _, a, _ = _setup(app)
    ser = za.post('/api/trocas', json={'turno_origem_id': a,
                                       'para_sigla': 'ZZC'}).get_json()['troca']
    assert ser['criado_em'].endswith('+00:00')
    assert ser['turno_origem']['quando'].startswith(ser['turno_origem']['dia'] + ' ')


def test_colegas_endpoint_marks_who_is_busy(app):
    za, _, _, _, a, _ = _setup(app)
    colegas = za.get(f'/api/trocas/colegas?turno={a}').get_json()['colegas']
    by = {c['sigla']: c for c in colegas}
    assert 'ZZA' not in by
    assert by['ZZB']['ocupado'].startswith('10:00-18:30')
    assert by['ZZC']['ocupado'] == ''
    free = [c['ocupado'] == '' for c in colegas]
    assert free == sorted(free, reverse=True), 'free colleagues come first'


def test_shift_list_drops_past_days_and_flags_the_rest(app):
    today = date.today()
    with app.app.app_context():
        _people(app, 'ZZA', 'ZZB')
        # The active week is next week's draft: the current week must still be
        # listed (it used to be dropped as "before the anchor").
        nxt = _week(app, today - timedelta(days=today.weekday()) + timedelta(weeks=1))
        app.SemanaConfig.query.update({'ativa': False})
        nxt.ativa = True
        app.db.session.commit()
        later = _turno(app, 'ZZA', today + timedelta(days=1))
        _turno(app, 'ZZA', today - timedelta(days=1))
        b = _turno(app, 'ZZB', today + timedelta(days=1), loja='carmelitas')
    za = _login(app, 'ZZA')
    data = za.get('/api/trocas/turnos-do-colega?sigla=ZZA').get_json()
    assert [t['id'] for t in data['turnos']] == [later]
    listed = za.get(f'/api/trocas/turnos-do-colega?sigla=ZZB&troca={later}').get_json()
    assert listed['turnos'][0]['conflitos'] == []       # same hours, swapped: clean
    assert listed['turnos'][0]['em_pedido'] is False and listed['turnos'][0]['id'] == b


def test_minha_escala_offers_no_swap_for_past_or_promised_shifts(app):
    today = date.today()
    days = {'past': today - timedelta(days=1), 'free': today + timedelta(days=1),
            'promised': today + timedelta(days=2)}
    with app.app.app_context():
        _people(app, 'ZZA', 'ZZC')
        ids = {k: _turno(app, 'ZZA', d) for k, d in days.items()}
    za = _login(app, 'ZZA')
    za.post('/api/trocas', json={'turno_origem_id': ids['promised'], 'para_sigla': 'ZZC'})

    def page_for(day):
        week = app.week_codigo(day - timedelta(days=day.weekday()))
        body = za.get(f'/minha-escala?semana={week}').data.decode('utf-8')
        return body, re.findall(r'data-turno-id="(\d+)"[^>]*?onclick="novoPedidoDe\(this\)"',
                                body, re.S)

    _, buttons = page_for(days['past'])
    assert str(ids['past']) not in buttons, 'a shift already worked offers a swap'
    _, buttons = page_for(days['free'])
    assert str(ids['free']) in buttons
    body, buttons = page_for(days['promised'])
    assert str(ids['promised']) not in buttons
    assert 'Pedido de troca em curso' in body


def test_trocas_page_leads_with_what_needs_doing(app):
    za, zb, _, _, a, b = _setup(app)
    za.post('/api/trocas', json={'turno_origem_id': a, 'para_sigla': 'ZZB',
                                 'turno_destino_id': b})
    body = zb.get('/trocas').data.decode('utf-8')
    assert body.index('Por decidir') < body.index('tr-sec-title">Central<')
    # No informal register, no internal week code in the shift lines.
    assert not re.search(r'\bQui \d+ · \d+-\d+[a-z]{3}\b', body)


# ── news: what happened to my requests since I last looked ───────────────────

def _badge(client):
    return client.get('/api/trocas/contagem').get_json()['pendentes']


def test_people_are_told_when_their_request_moves(app):
    """The badge used to count only things to DO: an author whose swap was
    approved or refused — or a colleague whose accepted swap was decided —
    got no signal at all."""
    za, zb, _, sup, a, b = _setup(app)
    tid = za.post('/api/trocas', json={'turno_origem_id': a, 'para_sigla': 'ZZB',
                                       'turno_destino_id': b}).get_json()['troca']['id']
    za.get('/trocas')                      # marks "seen"
    zb.get('/trocas')
    assert _badge(za) == 0
    zb.post(f'/api/trocas/{tid}/responder', json={'aceitar': True})
    assert _badge(za) == 1                 # the colleague accepted
    assert _badge(zb) == 0                 # accepting yourself is not news
    sup.post(f'/api/trocas/{tid}/decidir', json={'aprovar': True})
    assert _badge(zb) == 1                 # approved
    body = za.get('/trocas').data.decode('utf-8')
    assert f'id="card-{tid}"' in body and 'class="tc-novo"' in body
    assert _badge(za) == 0                 # seen now
    assert 'class="tc-novo"' not in za.get('/trocas').data.decode('utf-8')


def test_own_actions_are_never_news(app):
    za, _, zc, _, a, _ = _setup(app)
    za.get('/trocas')
    zc.get('/trocas')
    tid = za.post('/api/trocas', json={'turno_origem_id': a,
                                       'para_sigla': 'ZZC'}).get_json()['troca']['id']
    assert _badge(za) == 0                 # sending is not news for the sender
    assert _badge(zc) == 0                 # nomination is not a recipient decision
    zc.post(f'/api/trocas/{tid}/candidatar', json={})
    assert _badge(zc) == 0                 # one's own application is not news
    za.get('/trocas')
    za.post(f'/api/trocas/{tid}/cancelar')
    assert _badge(za) == 0                 # nor is cancelling
    assert _badge(zc) == 1                 # no longer to decide — but news: cancelled
